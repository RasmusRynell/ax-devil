"""Workspace Items: saved recipes that resolve into Content, and the registry of item kinds."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar, TypeVar
from uuid import uuid4

from ax_devil.modules.settings.config_manager import expand_environment_reference
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.content import Content, LiveOverlayMode
from ax_devil.modules.workspace.core.intake import OverlayFile, WorkspaceIntake, is_video_file
from ax_devil.modules.workspace.core.resolution import ItemResolutionError, PlaylistSettings, ResolutionContext

logger = get_logger(__name__)

_ItemT = TypeVar("_ItemT", bound="WorkspaceItem")
_ValueT = TypeVar("_ValueT")
_REQUIRED: Any = object()


def _read(data: Mapping[str, Any], key: str, expected: type[_ValueT], default: _ValueT = _REQUIRED) -> _ValueT:
    """Return ``data[key]`` as an *expected* JSON value, or *default* when absent; raise ``ValueError`` otherwise."""
    if key not in data:
        if default is _REQUIRED:
            raise ValueError(f"missing '{key}'")
        return default
    value = data[key]
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ValueError(f"'{key}' must be a {expected.__name__}")
    return value


def _read_optional_str(data: Mapping[str, Any], key: str) -> str | None:
    """Return ``data[key]`` as a string or None when absent or null; raise ``ValueError`` for anything else."""
    return None if data.get(key) is None else _read(data, key, str)


def _path_to_json(path: Path, base_dir: Path) -> str:
    """Return *path* relative to *base_dir* with POSIX separators when inside it, absolute otherwise.

    Both are normalized lexically first, so ``..`` segments cannot escape the folder; symlinks are not resolved.
    """
    absolute = Path(os.path.normpath(path.absolute()))
    folder = Path(os.path.normpath(base_dir.absolute()))
    try:
        return absolute.relative_to(folder).as_posix()
    except ValueError:
        return str(absolute)


def _path_from_json(data: Mapping[str, Any], key: str, base_dir: Path) -> Path:
    """Return the path in ``data[key]``; a relative one is read against *base_dir*."""
    return base_dir / _read(data, key, str)


def new_item_id() -> str:
    """Return a fresh item id; an item keeps its id for as long as it exists."""
    return uuid4().hex


@dataclass(frozen=True, kw_only=True)
class WorkspaceItem(ABC):
    """A recipe for one thing to work with: enough to rebuild its Content, never the rebuilt result.

    A kind is one subclass with a unique ``kind`` registered in ``ITEM_KINDS``; it implements ``_build_contents`` and
    the JSON hooks ``_fields_to_json`` and ``_fields_from_json`` for its own fields. Callers use ``resolve``, which also
    gives every Content its identity, and ``to_json`` / ``from_json``, which add the common fields.
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

    def to_json(self, base_dir: Path) -> Any:
        """Return this item as a JSON object; paths inside *base_dir* are written relative to it.

        Typed ``Any`` because an unreadable item returns whatever JSON value the file held.
        """
        return {"kind": self.kind, "id": self.id, "label": self.label, **self._fields_to_json(base_dir)}

    @classmethod
    def from_json(cls: type[_ItemT], data: Mapping[str, Any], base_dir: Path) -> _ItemT:
        """Return the item *data* describes; raise ``ValueError`` saying what is malformed.

        Relative paths are read against *base_dir*.
        """
        try:
            return cls(
                id=_read(data, "id", str), label=_read(data, "label", str), **cls._fields_from_json(data, base_dir)
            )
        except ValueError as exc:
            raise ValueError(f"Malformed {cls.kind} item: {exc}") from exc

    @abstractmethod
    def _fields_to_json(self, base_dir: Path) -> dict[str, Any]:
        """Return this kind's own JSON fields."""

    @classmethod
    @abstractmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
        """Return the constructor arguments for this kind's own fields; raise ``ValueError`` when malformed."""


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

    def _fields_to_json(self, base_dir: Path) -> dict[str, Any]:
        """Return the video path and the overlay files with their decoders."""
        return {
            "video": _path_to_json(self.video, base_dir),
            "overlays": [
                {"path": _path_to_json(overlay.path, base_dir), "decoder": overlay.decoder} for overlay in self.overlays
            ],
        }

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
        """Read the video path and the overlay files."""
        entries = _read(data, "overlays", list, [])
        if not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("'overlays' must be a list of objects")
        overlays = tuple(
            OverlayFile(_path_from_json(entry, "path", base_dir), _read(entry, "decoder", str)) for entry in entries
        )
        return {"video": _path_from_json(data, "video", base_dir), "overlays": overlays}

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


_LIVE_STR_FIELDS = (
    "host",
    "username",
    "password",
    "resolution",
    "mqtt_host",
    "mqtt_username",
    "mqtt_password",
    "analytics_data_source_key",
    "device_api_protocol",
    "websocket_topic",
)
_LIVE_INT_FIELDS = ("camera_head", "mqtt_port", "websocket_channel_id")


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

    def _fields_to_json(self, base_dir: Path) -> dict[str, Any]:
        """Return every field as stored; references stay references and the overlay mode is its value string."""
        return {
            name: getattr(self, name).value if name == "overlay_mode" else getattr(self, name)
            for name in (*_LIVE_STR_FIELDS, *_LIVE_INT_FIELDS, "stream_url", "overlay_mode", "handler_type")
        }

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
        """Read every field, using the defaults for absent ones."""
        defaults = cls(label="", host="")
        return {
            **{name: _read(data, name, str, getattr(defaults, name)) for name in _LIVE_STR_FIELDS},
            **{name: _read(data, name, int, getattr(defaults, name)) for name in _LIVE_INT_FIELDS},
            "stream_url": _read_optional_str(data, "stream_url"),
            "handler_type": _read_optional_str(data, "handler_type"),
            "overlay_mode": LiveOverlayMode.from_value(_read(data, "overlay_mode", str, defaults.overlay_mode.value)),
        }

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

    def _fields_to_json(self, base_dir: Path) -> dict[str, Any]:
        """Return the resolver id and its settings exactly as given; the core does not know which are paths."""
        return {"resolver": self.resolver, "settings": dict(self.settings)}

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
        """Read the resolver id and its settings as they were written."""
        return {"resolver": _read(data, "resolver", str), "settings": _read(data, "settings", dict, {})}

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


@dataclass(frozen=True, kw_only=True)
class UnreadableItem(WorkspaceItem):
    """An item the file held but this version cannot read: an unknown kind, or a known kind with malformed JSON.

    It never resolves, and it is written back exactly as it was read, so saving does not lose it.
    """

    kind: ClassVar[str] = "unreadable"
    raw: Any
    reason: str

    @classmethod
    def from_raw(cls, raw: Any, reason: str) -> UnreadableItem:
        """Return the item standing in for *raw*, keeping its id and label when it has them."""
        fields: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        item_id = fields.get("id")
        label = fields.get("label") or fields.get("kind")
        return cls(
            raw=raw,
            reason=reason,
            label=label if isinstance(label, str) else "Unreadable item",
            **({"id": item_id} if isinstance(item_id, str) else {}),
        )

    def _build_contents(self, context: ResolutionContext) -> Sequence[Content]:
        """Fail with the reason this item could not be read."""
        raise ItemResolutionError(self.reason)

    def to_json(self, base_dir: Path) -> Any:
        """Return the original JSON unchanged."""
        return self.raw

    def _fields_to_json(self, base_dir: Path) -> dict[str, Any]:
        return {}

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
        raise ValueError("unreadable items are only made from raw JSON")


ITEM_KINDS: dict[str, type[WorkspaceItem]] = {kind.kind: kind for kind in (VideoItem, LiveStreamItem, PlaylistItem)}
"""Every built-in item kind, keyed by its ``kind``."""


def item_from_json(data: Any, base_dir: Path) -> WorkspaceItem:
    """Return the item *data* describes, or an ``UnreadableItem`` when its kind is unknown or its JSON malformed."""
    if not isinstance(data, Mapping):
        return UnreadableItem.from_raw(data, "This item is not a JSON object.")
    kind = data.get("kind")
    item_type = ITEM_KINDS.get(kind) if isinstance(kind, str) else None
    if item_type is None:
        return UnreadableItem.from_raw(data, f"Unknown item kind: {kind!r}. A newer version or a missing plugin?")
    try:
        return item_type.from_json(data, base_dir)
    except ValueError as exc:
        return UnreadableItem.from_raw(data, str(exc))


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
