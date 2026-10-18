"""Workspace Items: saved recipes that resolve into Content, and the registry of item kinds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.source_paths import MAX_FOLDERS, SourcePaths, SourceSnapshot
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.content import Content, numbered_when_shared
from ax_devil.modules.workspace.core.intake import OverlayFile, WorkspaceIntake, is_video_file
from ax_devil.modules.workspace.core.item_base import (
    WorkspaceItem,
    new_item_id,
    path_from_json,
    path_to_json,
    read_field,
)
from ax_devil.modules.workspace.core.live_stream_item import LiveStreamItem
from ax_devil.modules.workspace.core.resolution import ItemResolutionError, PlaylistSettings, ResolutionContext

logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class VideoItem(WorkspaceItem):
    """A video file with optional overlay files, each read by a file decoder."""

    kind: ClassVar[str] = "video"
    bookmarkable: ClassVar[bool] = True
    video: Path
    overlays: tuple[OverlayFile, ...] = ()

    @property
    def default_name(self) -> str:
        """Return the video's file name."""
        return self.video.name

    def _fields_to_json(self, base_dir: Path | None) -> dict[str, Any]:
        """Return the video path and the overlay files with their decoders."""
        return {
            "video": path_to_json(self.video, base_dir),
            "overlays": [
                {"path": path_to_json(overlay.path, base_dir), "decoder": overlay.decoder} for overlay in self.overlays
            ],
        }

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path | None) -> dict[str, Any]:
        """Read the video path and the overlay files."""
        entries = read_field(data, "overlays", list, [])
        if not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("'overlays' must be a list of objects")
        overlays = tuple(
            OverlayFile(path_from_json(entry, "path", base_dir), read_field(entry, "decoder", str)) for entry in entries
        )
        return {"video": path_from_json(data, "video", base_dir), "overlays": overlays}

    def source_paths(self, context: ResolutionContext, token: CancelToken) -> SourcePaths:
        """Return the overlay files; changes to the video file do not matter."""
        return SourcePaths(files=frozenset(overlay.path for overlay in self.overlays))

    def _build_contents(self, context: ResolutionContext, token: CancelToken) -> Sequence[Content]:
        """Return the seekable video, after checking that its files exist."""
        for path in token.polled((self.video, *(overlay.path for overlay in self.overlays))):
            if not path.is_file():
                raise ItemResolutionError(f"File not found: {path}")
        return (
            context.intake.create_seekable_video(
                video_path=self.video, display_name=self.default_name, overlays=self.overlays
            ),
        )


@dataclass(frozen=True, kw_only=True)
class PlaylistItem(WorkspaceItem):
    """A playlist resolver id plus that resolver's JSON-serializable settings."""

    kind: ClassVar[str] = "playlist"
    bookmarkable: ClassVar[bool] = True
    resolver: str
    settings: PlaylistSettings = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Keep a private copy of the settings, so later changes to the caller's mapping never reach the item."""
        object.__setattr__(self, "settings", deepcopy(self.settings))

    def _fields_to_json(self, base_dir: Path | None) -> dict[str, Any]:
        """Return the resolver id and its settings exactly as given; the core does not know which are paths."""
        return {"resolver": self.resolver, "settings": dict(self.settings)}

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path | None) -> dict[str, Any]:
        """Read the resolver id and its settings as they were written."""
        return {"resolver": read_field(data, "resolver", str), "settings": read_field(data, "settings", dict, {})}

    @property
    def default_name(self) -> str:
        """Return the resolver id; resolved playlists keep the names their resolver gives them."""
        return self.resolver

    def source_paths(self, context: ResolutionContext, token: CancelToken) -> SourcePaths:
        """Return the paths the resolver reads from, or none when it is missing or names none."""
        try:
            return context.playlist_resolver(self.resolver).source_paths(deepcopy(self.settings), token)
        except ItemResolutionError:
            return SourcePaths()  # Resolving reports the missing resolver.
        except Exception as exc:  # A plugin may fail in any way; resolving reports its failure.
            logger.warning(f"Playlist resolver '{self.resolver}' could not name its source paths: {exc}")
            return SourcePaths()

    def _build_contents(self, context: ResolutionContext, token: CancelToken) -> Sequence[Content]:
        """Run the resolver with a copy of the settings, so a plugin cannot change the item's settings.

        Playlists that share a name get it numbered " (2)", " (3)", so their entries' Bookmark Target keys differ. A
        playlist's ``content_id`` derives from that name, so it names the same playlist when the resolver returns a
        different set.
        """
        try:
            playlists = context.playlist_resolver(self.resolver).resolve(deepcopy(self.settings), token)
            names = numbered_when_shared(playlist.source_name for playlist in playlists)
            return [
                replace(playlist, source_name=name, content_id=f"{self.id}/{name}")
                for playlist, name in zip(playlists, names, strict=True)
            ]
        except ItemResolutionError:
            raise
        except Exception as exc:  # A plugin may fail in any way; the item stays, with the reason.
            raise ItemResolutionError(f"Playlist resolver '{self.resolver}' failed: {exc}") from exc

    def name_contents(self, contents: Sequence[Content]) -> tuple[Content, ...]:
        """Name each playlist with the shared rule: the label alone, or ``label / playlist`` when there are several."""
        if not self.label:
            return tuple(contents)
        several = len(contents) > 1
        return tuple(
            replace(content, display_name=_playlist_display_name(self.label, content.display_name, several=several))
            for content in contents
        )


def _playlist_display_name(label: str, playlist_name: str, *, several: bool) -> str:
    """Return the display name of one playlist of a Playlist Item: the label alone, or ``label / playlist``."""
    return f"{label} / {playlist_name}" if several else label


@dataclass(frozen=True, kw_only=True)
class UnreadableItem(WorkspaceItem):
    """An item the file held but this version cannot read: an unknown kind, or a known kind with malformed JSON.

    It never resolves, and it is written back exactly as it was read, so saving does not lose it. Its JSON is not
    understood, so relative paths in it are not rewritten when the workspace is saved to another folder. Raw JSON
    without an id gets a new id on every read, so unreadable items compare by what they hold, not by id; a reopened
    workspace is then not modified by them alone.
    """

    kind: ClassVar[str] = "unreadable"
    renamable: ClassVar[bool] = False
    id: str = field(default_factory=new_item_id, compare=False)
    raw: Any
    reason: str

    @classmethod
    def from_raw(cls, raw: Any, reason: str) -> UnreadableItem:
        """Return the item standing in for *raw*, keeping its id and label when it has them."""
        fields: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        item_id = fields.get("id")
        label = fields.get("label")
        return cls(
            raw=raw,
            reason=reason,
            label=label if isinstance(label, str) else "",
            **({"id": item_id} if isinstance(item_id, str) else {}),
        )

    @property
    def default_name(self) -> str:
        """Return the kind the file named, or a generic name."""
        kind = self.raw.get("kind") if isinstance(self.raw, Mapping) else None
        return kind if isinstance(kind, str) and kind else "Unreadable item"

    def _build_contents(self, context: ResolutionContext, token: CancelToken) -> Sequence[Content]:
        """Fail with the reason this item could not be read."""
        raise ItemResolutionError(self.reason)

    def to_json(self, base_dir: Path | None) -> Any:
        """Return the original JSON unchanged."""
        return self.raw

    def _fields_to_json(self, base_dir: Path | None) -> dict[str, Any]:
        return {}

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path | None) -> dict[str, Any]:
        raise ValueError("unreadable items are only made from raw JSON")


@dataclass(frozen=True)
class ItemResolution:
    """What one item resolved to: its Content, the error that kept it from resolving, or neither while it resolves.

    The result keeps the Content as named without a label and derives ``contents`` from the item's label, so a renamed
    item is named again by ``renamed`` and never resolved twice. Build results with ``of`` or ``pending``.

    *sources* is what the item and its Content were read from, as it was on disk before it was read.
    """

    item: WorkspaceItem
    error: ItemResolutionError | None = None
    by_default_name: tuple[Content, ...] = field(default=(), repr=False)
    sources: SourceSnapshot = field(default_factory=lambda: SourceSnapshot(SourcePaths()), repr=False)
    contents: tuple[Content, ...] = field(init=False)

    def __post_init__(self) -> None:
        """Name the Content after the item."""
        object.__setattr__(self, "contents", self.item.name_contents(self.by_default_name))

    @classmethod
    def of(cls, item: WorkspaceItem, context: ResolutionContext, token: CancelToken) -> ItemResolution:
        """Resolve *item*; a failure is recorded as the result's error instead of raised.

        The item's source paths are fingerprinted before anything is read, so a change made while resolving is
        reported once the result is watched. A path the Content names too keeps that fingerprint.
        """
        sources = item.source_paths(context, token).snapshot(token)
        contents: tuple[Content, ...] = ()
        error: ItemResolutionError | None = None
        try:
            contents = item._resolve_by_default_name(context, token)
        except ItemResolutionError as exc:
            error = exc
        sources |= SourcePaths.merged(content.source_paths for content in contents).snapshot(token)
        if sources.truncated:
            logger.warning(f"{item.display_name} spans over {MAX_FOLDERS} folders; changes in the rest go unnoticed")
        return cls(item, error=error, by_default_name=contents, sources=sources)

    @classmethod
    def pending(cls, item: WorkspaceItem) -> ItemResolution:
        """Return the result of *item* while it is still being resolved."""
        return cls(item)

    @property
    def is_pending(self) -> bool:
        """Return whether the item is still being resolved."""
        return self.error is None and not self.by_default_name

    def renamed(self, item: WorkspaceItem) -> ItemResolution:
        """Return this result for *item*, the same item under another label, with its Content named again."""
        return replace(self, item=item)


ITEM_KINDS: dict[str, type[WorkspaceItem]] = {kind.kind: kind for kind in (VideoItem, LiveStreamItem, PlaylistItem)}
"""Every built-in item kind, keyed by its ``kind``."""


def item_from_json(data: Any, base_dir: Path | None) -> WorkspaceItem:
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
class OverlayChoice:
    """An overlay file picked for a video, with the decoder that reads it once one is chosen."""

    path: Path
    decoder: str | None = None


def overlay_choice(path: Path, intake: WorkspaceIntake) -> OverlayChoice | None:
    """Return *path* as an overlay choice, with its decoder set when exactly one may read it.

    Returns None when no decoder reads the file. A choice without a decoder is ambiguous: several may read it.
    """
    decoders = intake.file_decoder_options_for(path)
    if not decoders:
        return None
    return OverlayChoice(path, decoders[0].handler_type if len(decoders) == 1 else None)


@dataclass(frozen=True, slots=True)
class VideoFileSelection:
    """A video file with the overlay files picked for it, some of which may still need a decoder choice."""

    video: Path
    overlays: tuple[OverlayChoice, ...] = ()

    @property
    def needs_decoder(self) -> bool:
        """Return whether any overlay file still waits for a decoder choice."""
        return any(overlay.decoder is None for overlay in self.overlays)

    def to_item(self, label: str = "") -> VideoItem:
        """Return the Video Item for this selection, named *label* or, when empty, after the video file.

        An overlay still waiting for its decoder is left out.
        """
        return VideoItem(
            label=label,
            video=self.video,
            overlays=tuple(
                OverlayFile(overlay.path, overlay.decoder) for overlay in self.overlays if overlay.decoder is not None
            ),
        )


def video_file_selections(paths: Sequence[Path], intake: WorkspaceIntake) -> tuple[VideoFileSelection, ...]:
    """Pair dropped or picked files into video selections.

    Every video file opens on its own. One video with other files takes each that a decoder can read as an overlay,
    with the decoder chosen when exactly one may read it; otherwise the selection still needs a decoder choice.
    """
    videos = [path for path in paths if is_video_file(path)]
    others = [path for path in paths if not is_video_file(path)]
    if len(videos) != 1:
        if others:
            logger.info(f"Ignoring {len(others)} non-video file(s) that do not pair with exactly one video")
        return tuple(VideoFileSelection(video) for video in videos)
    overlays: list[OverlayChoice] = []
    for path in others:
        choice = overlay_choice(path, intake)
        if choice is None:
            logger.info(f"No data handler reads {path.name}; opening {videos[0].name} without it")
        else:
            overlays.append(choice)
    return (VideoFileSelection(videos[0], tuple(overlays)),)
