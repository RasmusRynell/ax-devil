"""Workspace item labels and information payload builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ax_devil.modules.workspace.content import (
    Content,
    EntryLane,
    FileOverlaySourceSpec,
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
    OverlaySourceKind,
    OverlaySourceSpec,
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


def _video_source_fields(content: SeekableVideoContent | LiveVideoContent) -> list[tuple[str, str]]:
    """Return standard information fields owned by a video's source specification."""
    if isinstance(content, SeekableVideoContent):
        source = content.source_spec
        fields = [("path", str(source.path))]
        image_sequence = source.image_sequence_config
        if image_sequence is not None:
            fields.extend(
                [
                    ("fps", _format_info_value(image_sequence.fps)),
                    ("width", str(image_sequence.width)),
                    ("height", str(image_sequence.height)),
                    ("frames", str(image_sequence.total_frames)),
                ]
            )
        return fields
    live_source = content.source_spec
    return [
        ("host", live_source.host),
        ("username", live_source.username),
        ("camera head", str(live_source.camera_head)),
        ("resolution", live_source.resolution),
        ("stream URL", live_source.stream_url or "(default)"),
    ]


def _overlay_source_fields(source: OverlaySourceSpec) -> list[tuple[str, str]]:
    """Return standard information fields owned by an overlay source specification."""
    if isinstance(source, FileOverlaySourceSpec):
        return [("path", str(source.path)), ("handler type", source.handler_type)]
    if isinstance(source, LiveRTSPOverlaySourceSpec):
        return [("handler type", source.handler_type)]
    if isinstance(source, LiveMQTTOverlaySourceSpec):
        return [
            ("handler type", source.handler_type),
            ("broker host", source.broker_host),
            ("broker port", str(source.broker_port)),
            ("broker username", source.broker_username or "(none)"),
            ("data source", source.analytics_data_source_key),
            ("device API protocol", source.device_api_protocol),
        ]
    assert isinstance(source, LiveWebSocketOverlaySourceSpec)
    return [
        ("handler type", source.handler_type),
        ("topic", source.topic),
        ("channel ID", str(source.channel_id)),
        ("device API protocol", source.device_api_protocol),
    ]


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


def build_content_information(content: Content) -> WorkspaceItemInfo:
    """Return information payload for a top-level content item."""
    if isinstance(content, PlaylistContent):
        lane_count = sum(len(entry.lanes) for entry in content.entries)
        fields = [
            ("type", "playlist"),
            ("entries", str(len(content.entries))),
            ("lanes", str(lane_count)),
        ]
        fields.extend(_metadata_fields(content.metadata))
        children = [
            build_playlist_entry_information(content, entry, entry_index)
            for entry_index, entry in enumerate(content.entries)
        ]
        return _build_information(content.display_name, fields, children)

    if isinstance(content, SeekableVideoContent):
        fields = [
            ("type", "seekable video"),
            ("overlays", str(len(content.overlays))),
        ]
    else:
        fields = [
            ("type", "live video"),
            ("overlays", str(len(content.overlays))),
        ]
    fields.extend(_video_source_fields(content))
    fields.extend(_metadata_fields(content.metadata))
    lanes = content.standalone_lanes()
    children = [
        build_video_lane_information(content, lane) for lane in lanes if lane.source_kind != OverlaySourceKind.NO_SOURCE
    ]
    return _build_information(content.display_name, fields, children)


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
        fields.extend(_overlay_source_fields(lane.overlay.source_spec))
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
        fields.extend(_overlay_source_fields(lane.overlay.source_spec))
        fields.extend(_metadata_fields(lane.overlay.metadata))
    fields.extend(_metadata_fields(lane.metadata))
    return _build_information(lane.display_name, fields)
