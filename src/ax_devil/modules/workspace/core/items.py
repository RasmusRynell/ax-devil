"""Workspace Items: saved recipes that resolve into Content, and the registry of item kinds."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar, TypeVar
from uuid import uuid4

from ax_devil.modules.settings.config_manager import expand_environment_reference
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.content import Content, LiveOverlayMode
from ax_devil.modules.workspace.core.intake import OverlayFile, WorkspaceIntake, is_video_file
from ax_devil.modules.workspace.core.resolution import ItemResolutionError, PlaylistSettings, ResolutionContext

logger = get_logger(__name__)

_ItemT = TypeVar("_ItemT", bound="WorkspaceItem")


def new_item_id() -> str:
    """Return a fresh item id; an item keeps its id for as long as it exists."""
    return uuid4().hex


@dataclass(frozen=True, kw_only=True)
class WorkspaceItem(ABC):
    """A recipe for one thing to work with: enough to rebuild its Content, never the rebuilt result.

    A kind is one subclass with a unique ``kind`` registered in ``ITEM_KINDS``; it implements ``_build_contents``.
    Callers use ``resolve``, which also gives every Content its identity.
    """

    kind: ClassVar[str]
    label: str
    id: str = field(default_factory=new_item_id)

    def with_label(self: _ItemT, label: str) -> _ItemT:
        """Return this item renamed to *label*, keeping its id."""
        return replace(self, label=label)

    def resolve(self, context: ResolutionContext) -> tuple[Content, ...]:
        """Rebuild this item's Content named after its label, or raise ``ItemResolutionError`` with a reason."""
        return self.name_contents(self.resolve_base(context))

    def resolve_base(self, context: ResolutionContext) -> tuple[Content, ...]:
        """Rebuild this item's Content before the label is applied, or raise ``ItemResolutionError``.

        Content ids derive from the item id and the Content's position, so they are the same on every resolution.
        Renaming never calls this; it calls ``name_contents`` on the result that was kept.
        """
        try:
            contents = self._build_contents(context)
        except (ValueError, OSError) as exc:
            raise ItemResolutionError(str(exc)) from exc
        if not contents:
            raise ItemResolutionError(f"{self.label} has nothing to open.")
        return tuple(
            replace(content, content_id=f"{self.id}/{index}", item_id=self.id) for index, content in enumerate(contents)
        )

    def name_contents(self, base: Sequence[Content]) -> tuple[Content, ...]:
        """Return *base* with display names taken from this item's label; the default names every Content by it."""
        return tuple(replace(content, display_name=self.label) for content in base)

    @abstractmethod
    def _build_contents(self, context: ResolutionContext) -> Sequence[Content]:
        """Return this item's Content; raise ``ItemResolutionError``, ``ValueError``, or ``OSError`` when it cannot."""


@dataclass(frozen=True, kw_only=True)
class VideoItem(WorkspaceItem):
    """A video file with optional overlay files, each read by a file decoder."""

    kind: ClassVar[str] = "video"
    video: Path
    overlays: tuple[OverlayFile, ...] = ()

    @property
    def description(self) -> str:
        """Return the full file paths behind this item."""
        return "\n".join([str(self.video), *(f"Overlay: {overlay.path}" for overlay in self.overlays)])

    def _build_contents(self, context: ResolutionContext) -> Sequence[Content]:
        """Return the seekable video, after checking that its files exist."""
        for path in (self.video, *(overlay.path for overlay in self.overlays)):
            if not path.is_file():
                raise ItemResolutionError(f"File not found: {path}")
        return (
            context.intake.create_seekable_video(
                video_path=self.video, display_name=self.label, overlays=self.overlays
            ),
        )


@dataclass(frozen=True, kw_only=True)
class LiveStreamItem(WorkspaceItem):
    """A camera stream with optional live overlay settings.

    The device and MQTT broker hosts, usernames, and passwords are stored as entered: a ``$VARIABLE`` reference stays a
    reference and is expanded only when the item resolves, using the config's rule.
    """

    kind: ClassVar[str] = "live_stream"
    host: str
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    camera_head: int = 1
    resolution: str = "1280x720"
    stream_url: str | None = field(default=None, repr=False)
    overlay_mode: LiveOverlayMode = LiveOverlayMode.NONE
    handler_type: str | None = None
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = field(default="", repr=False)
    mqtt_password: str = field(default="", repr=False)
    analytics_data_source_key: str = ""
    device_api_protocol: str = "https"
    websocket_topic: str = ""
    websocket_channel_id: int = 1

    def expanded(self) -> LiveStreamItem:
        """Return this item with its environment references replaced by their current values."""
        return replace(
            self,
            host=expand_environment_reference(self.host),
            username=expand_environment_reference(self.username),
            password=expand_environment_reference(self.password),
            mqtt_host=expand_environment_reference(self.mqtt_host),
            mqtt_username=expand_environment_reference(self.mqtt_username),
            mqtt_password=expand_environment_reference(self.mqtt_password),
        )

    def _build_contents(self, context: ResolutionContext) -> Sequence[Content]:
        """Return the live stream, built from the expanded connection values."""
        values = self.expanded()
        return (
            context.intake.create_live_stream(
                display_name=self.label,
                host=values.host,
                username=values.username,
                password=values.password,
                camera_head=values.camera_head,
                resolution=values.resolution,
                stream_url=values.stream_url,
                overlay_mode=values.overlay_mode,
                handler_type=values.handler_type,
                mqtt_host=values.mqtt_host,
                mqtt_port=values.mqtt_port,
                mqtt_username=values.mqtt_username,
                mqtt_password=values.mqtt_password,
                analytics_data_source_key=values.analytics_data_source_key,
                device_api_protocol=values.device_api_protocol,
                websocket_topic=values.websocket_topic,
                websocket_channel_id=values.websocket_channel_id,
            ),
        )


@dataclass(frozen=True, kw_only=True)
class PlaylistItem(WorkspaceItem):
    """A playlist resolver id plus that resolver's JSON-serializable settings."""

    kind: ClassVar[str] = "playlist"
    resolver: str
    settings: PlaylistSettings = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Keep a private copy of the settings, so later changes to the caller's mapping never reach the item."""
        object.__setattr__(self, "settings", deepcopy(self.settings))

    def _build_contents(self, context: ResolutionContext) -> Sequence[Content]:
        """Run the resolver with a copy of the settings, so a plugin cannot change the item's settings."""
        try:
            return context.playlist_resolver(self.resolver).resolve(deepcopy(self.settings))
        except ItemResolutionError:
            raise
        except Exception as exc:  # A plugin may fail in any way; the item stays, with the reason.
            raise ItemResolutionError(f"Playlist resolver '{self.resolver}' failed: {exc}") from exc

    def name_contents(self, base: Sequence[Content]) -> tuple[Content, ...]:
        """Name each playlist with the shared rule: the label alone, or ``label / playlist`` when there are several."""
        several = len(base) > 1
        return tuple(
            replace(content, display_name=_playlist_display_name(self.label, content.display_name, several=several))
            for content in base
        )


def _playlist_display_name(label: str, playlist_name: str, *, several: bool) -> str:
    """Return the display name of one playlist of a Playlist Item: the label alone, or ``label / playlist``."""
    return f"{label} / {playlist_name}" if several else label


ITEM_KINDS: dict[str, type[WorkspaceItem]] = {kind.kind: kind for kind in (VideoItem, LiveStreamItem, PlaylistItem)}
"""Every built-in item kind, keyed by its ``kind``."""


@dataclass(frozen=True, slots=True)
class VideoFileSelection:
    """A video file with an optional overlay file whose decoder may still need choosing."""

    video: Path
    overlay: Path | None = None
    decoder: str | None = None

    @property
    def needs_decoder(self) -> bool:
        """Return whether the overlay file still waits for a decoder choice."""
        return self.overlay is not None and self.decoder is None

    def to_item(self, label: str = "") -> VideoItem:
        """Return the Video Item for this selection, named *label* or else after the video file.

        An overlay still waiting for its decoder is left out.
        """
        overlays = (OverlayFile(self.overlay, self.decoder),) if self.overlay is not None and self.decoder else ()
        return VideoItem(label=label or self.video.name, video=self.video, overlays=overlays)


def video_file_selections(paths: Sequence[Path], intake: WorkspaceIntake) -> tuple[VideoFileSelection, ...]:
    """Pair dropped or picked files into video selections.

    Every video file opens on its own. One video with one other file takes that file as its overlay, with the decoder
    chosen when exactly one may read it; otherwise the selection still needs a decoder choice.
    """
    videos = [path for path in paths if is_video_file(path)]
    others = [path for path in paths if not is_video_file(path)]
    if len(videos) != 1 or len(others) != 1:
        if others:
            logger.info(f"Ignoring {len(others)} non-video file(s) that do not pair with exactly one video")
        return tuple(VideoFileSelection(video) for video in videos)
    overlay = others[0]
    decoders = intake.file_decoder_options_for(overlay)
    if not decoders:
        logger.info(f"No data handler reads {overlay.name}; opening {videos[0].name} without an overlay")
        return (VideoFileSelection(videos[0]),)
    decoder = decoders[0].handler_type if len(decoders) == 1 else None
    return (VideoFileSelection(videos[0], overlay, decoder),)
