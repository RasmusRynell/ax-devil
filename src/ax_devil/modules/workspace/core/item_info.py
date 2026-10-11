"""Workspace item labels and information payload builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ax_devil.modules.workspace.core.content import (
    EntryLane,
    InfoFields,
    LiveVideoContent,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)


@dataclass(frozen=True, slots=True)
class WorkspaceItemInfo:
    """Structured information for one workspace item and its nested children."""

    title: str
    fields: tuple[tuple[str, str], ...]
    children: tuple["WorkspaceItemInfo", ...] = field(default_factory=tuple)


def _format_info_value(value: Any) -> str:
    """Convert metadata values into stable human-readable text."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(_format_info_value(item) for item in value) or "(empty)"
    return str(value)


def _metadata_fields(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    """Render metadata dictionary entries as labeled values."""
    return [(key, _format_info_value(metadata[key])) for key in sorted(metadata)]


def _info_fields(fields: InfoFields) -> list[tuple[str, str]]:
    """Render labeled values owned by content or source specifications."""
    return [(label, _format_info_value(value)) for label, value in fields]


def _build_information(
    title: str,
    fields: list[tuple[str, str]],
    children: list[WorkspaceItemInfo] | None = None,
) -> WorkspaceItemInfo:
    """Build one structured information payload."""
    return WorkspaceItemInfo(
        title=title,
        fields=tuple(fields),
        children=tuple(children or ()),
    )


def build_playlist_information(playlist: PlaylistContent) -> WorkspaceItemInfo:
    """Return information payload for a top-level playlist and its entries."""
    fields = [*_info_fields(playlist.info_fields()), *_metadata_fields(playlist.metadata)]
    children = [
        build_playlist_entry_information(playlist, entry, entry_index)
        for entry_index, entry in enumerate(playlist.entries)
    ]
    return _build_information(playlist.display_name, fields, children)


def build_video_information(video: SeekableVideoContent | LiveVideoContent) -> WorkspaceItemInfo:
    """Return information payload for a top-level video and its overlay lanes."""
    fields = [*_info_fields(video.info_fields()), *_metadata_fields(video.metadata)]
    children = [
        build_video_lane_information(video, lane)
        for lane in video.standalone_lanes()
        if lane.source_kind != OverlaySourceKind.NO_SOURCE
    ]
    return _build_information(video.display_name, fields, children)


def build_playlist_entry_information(
    playlist: PlaylistContent,
    entry: PlaylistEntry,
    entry_index: int,
) -> WorkspaceItemInfo:
    """Return information payload for one playlist entry row."""
    video_names = sorted({lane.video.display_name for lane in entry.lanes})
    fields = [
        ("type", "playlist entry"),
        ("playlist", playlist.display_name),
        ("entry", str(entry_index + 1)),
        ("lanes", str(len(entry.lanes))),
        ("videos", ", ".join(video_names)),
    ]
    fields.extend(_metadata_fields(entry.metadata))
    children: list[WorkspaceItemInfo] = []
    if entry.should_show_lane_children():
        children.extend(
            build_playlist_lane_information(playlist, entry, entry_index, lane, lane_index)
            for lane_index, lane in enumerate(entry.lanes)
        )
    return _build_information(f"{playlist.display_name} / Entry {entry_index + 1}", fields, children)


def build_video_lane_information(video: SeekableVideoContent | LiveVideoContent, lane: EntryLane) -> WorkspaceItemInfo:
    """Return information payload for an overlay lane under a standalone video."""
    fields = [
        ("type", "overlay"),
        ("video", video.display_name),
        ("lane", lane.display_name),
        ("overlay source", lane.source_kind.display_name),
    ]
    if lane.overlay is not None:
        fields.extend(_info_fields(lane.overlay.source_spec.info_fields()))
        fields.extend(_metadata_fields(lane.overlay.metadata))
    fields.extend(_metadata_fields(lane.metadata))
    return _build_information(lane.display_name, fields)


def build_playlist_lane_information(
    playlist: PlaylistContent,
    entry: PlaylistEntry,
    entry_index: int,
    lane: EntryLane,
    lane_index: int,
) -> WorkspaceItemInfo:
    """Return information payload for one overlay lane inside a playlist entry."""
    fields = [
        ("type", "overlay"),
        ("playlist", playlist.display_name),
        ("entry", str(entry_index + 1)),
        ("lane", f"{lane_index + 1} of {len(entry.lanes)}"),
        ("video", lane.video.display_name),
        ("overlay source", lane.source_kind.display_name),
    ]
    if lane.overlay is not None:
        fields.extend(_info_fields(lane.overlay.source_spec.info_fields()))
        fields.extend(_metadata_fields(lane.overlay.metadata))
    fields.extend(_metadata_fields(lane.metadata))
    return _build_information(lane.display_name, fields)


def build_unavailable_information(name: str, reason: str) -> WorkspaceItemInfo:
    """Build the information payload for an item that could not open, giving the reason."""
    return _build_information(name, [("Unavailable", reason)])
