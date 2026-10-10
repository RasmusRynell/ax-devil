"""Workspace-owned content descriptions and playlist structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, cast
from uuid import uuid4

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import ImageSequenceConfig
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy


class OverlaySourceKind(Enum):
    """Enumeration of overlay source kinds."""

    display_name: str
    description: str

    def __new__(cls, value: str, display_name: str, description: str = "") -> OverlaySourceKind:
        obj = object.__new__(cls)
        obj._value_ = value
        obj.display_name = display_name
        obj.description = description
        return obj

    FILE_SOURCE = ("file", "File", "Overlay loaded from file.")
    RTSP_SOURCE = ("rtsp", "RTSP", "Overlay embedded in RTSP stream.")
    MQTT_SOURCE = ("mqtt", "MQTT", "Overlay delivered over MQTT.")
    WEBSOCKET_SOURCE = ("websocket", "DataHub WebSocket", "Overlay delivered over Axis DataHub WebSocket.")
    NO_SOURCE = ("none", "None", "No overlay source.")

    def __str__(self) -> str:
        return str(self.value)


class LiveOverlayMode(str, Enum):
    """Supported overlay selections for a live workspace stream."""

    display_name: str
    requires_handler: bool
    description: str

    def __new__(cls, value: str, display_name: str, requires_handler: bool, description: str) -> LiveOverlayMode:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.display_name = display_name
        obj.requires_handler = requires_handler
        obj.description = description
        return obj

    NONE = ("none", "None", False, "Show the video only. Choose a mode to draw the device's analytics on it.")
    RTSP = ("rtsp", "RTSP Embedded", True, "Decode analytics metadata sent inside the RTSP video stream.")
    MQTT = ("mqtt", "MQTT", True, "Receive analytics that the device publishes to an MQTT broker.")
    WEBSOCKET = ("websocket", "DataHub WebSocket", True, "Subscribe to a DataHub analytics topic on the device.")

    def __str__(self) -> str:
        return cast(str, self.value)

    @classmethod
    def from_value(cls, value: str) -> LiveOverlayMode:
        """Return the mode represented by a config or CLI string."""
        mode = cast(LiveOverlayMode | None, cls._value2member_map_.get(value.lower()))
        if mode is None:
            raise ValueError(f"Unsupported live overlay mode: {value}")
        return mode


# ---------------------------------------------------------------------------
# Content model plumbing
# ---------------------------------------------------------------------------


def _new_content_id() -> str:
    return uuid4().hex


ConsiderationKind = Literal["playlist_entry", "playlist_lane", "video_lane"]


@dataclass(frozen=True, slots=True)
class ConsiderationItemRef:
    """Typed identifier for one Workspace item that can be considered or ignored."""

    kind: ConsiderationKind
    content_id: str
    entry_index: int
    lane_index: int | None = None

    @classmethod
    def playlist_entry(cls, content_id: str, entry_index: int) -> "ConsiderationItemRef":
        """Reference one playlist entry."""
        return cls(kind="playlist_entry", content_id=content_id, entry_index=entry_index)

    @classmethod
    def playlist_lane(cls, content_id: str, entry_index: int, lane_index: int) -> "ConsiderationItemRef":
        """Reference one lane inside a playlist entry."""
        return cls(kind="playlist_lane", content_id=content_id, entry_index=entry_index, lane_index=lane_index)

    @classmethod
    def video_lane(cls, content_id: str, lane_index: int) -> "ConsiderationItemRef":
        """Reference one overlay lane of standalone seekable video content."""
        return cls(kind="video_lane", content_id=content_id, entry_index=0, lane_index=lane_index)


@dataclass(frozen=True, slots=True)
class ConsiderationItem:
    """One supported consideration item and its initial state."""

    ref: ConsiderationItemRef
    default_considered: bool


@dataclass(frozen=True, slots=True)
class OnScreenWorkspaceItem:
    """One item currently shown by a workspace viewer."""

    kind: Literal["video", "playlist_entry"]
    content_id: str
    entry_index: int | None = None


InfoFields = tuple[tuple[str, object], ...]
"""Labeled values shown in the item information dialog, formatted by the dialog's payload builder."""


class ConsiderationQuery(Protocol):
    """Read-only consideration state consumed by Workspace viewers."""

    def is_item_considered(self, item_ref: ConsiderationItemRef) -> bool:
        """Return whether the referenced item participates in navigation or layout."""
        ...


@dataclass(frozen=True, slots=True)
class FileVideoSourceSpec:
    """Description of a seekable file video source."""

    path: Path
    image_sequence_config: ImageSequenceConfig | None = field(default=None, compare=False, hash=False)

    def info_fields(self) -> InfoFields:
        """Return the video file path and any image sequence timing."""
        image_sequence = self.image_sequence_config
        if image_sequence is None:
            return (("path", self.path),)
        return (
            ("path", self.path),
            ("fps", image_sequence.fps),
            ("width", image_sequence.width),
            ("height", image_sequence.height),
            ("frames", image_sequence.total_frames),
        )


@dataclass(frozen=True, slots=True)
class FileOverlaySourceSpec:
    """Description of a seekable file overlay source and decoder selection."""

    path: Path
    handler_type: str
    source_kind: ClassVar[OverlaySourceKind] = OverlaySourceKind.FILE_SOURCE
    timestamp_fallback_policy: TimestampFallbackPolicy = field(default_factory=TimestampFallbackPolicy)
    decoder_kwargs: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def source_location(self) -> str:
        """Return the overlay file path for source labels and tooltips."""
        return str(self.path)

    def info_fields(self) -> InfoFields:
        """Return the overlay file path and decoder."""
        return (("path", self.path), ("handler type", self.handler_type))


@dataclass(frozen=True, slots=True)
class LiveRTSPStreamSpec:
    """Description of an Axis RTSP video stream."""

    host: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    camera_head: int = 1
    resolution: str = "1280x720"
    stream_url: str | None = field(default=None, repr=False)

    def info_fields(self) -> InfoFields:
        """Return the device, camera head, and stream settings; never the password."""
        return (
            ("host", self.host),
            ("username", self.username),
            ("camera head", self.camera_head),
            ("resolution", self.resolution),
            ("stream URL", self.stream_url or "(default)"),
        )


@dataclass(frozen=True, slots=True)
class LiveRTSPOverlaySourceSpec:
    """Description of embedded RTSP analytics payloads and decoder selection."""

    handler_type: str
    source_kind: ClassVar[OverlaySourceKind] = OverlaySourceKind.RTSP_SOURCE
    source_location: ClassVar[None] = None

    def info_fields(self) -> InfoFields:
        """Return the decoder."""
        return (("handler type", self.handler_type),)


@dataclass(frozen=True, slots=True)
class LiveMQTTOverlaySourceSpec:
    """Description of MQTT analytics payloads and decoder selection."""

    handler_type: str
    broker_host: str
    broker_port: int = 1883
    broker_username: str = field(default="", repr=False)
    broker_password: str = field(default="", repr=False)
    analytics_data_source_key: str = ""
    device_api_protocol: str = "https"
    source_kind: ClassVar[OverlaySourceKind] = OverlaySourceKind.MQTT_SOURCE
    source_location: ClassVar[None] = None

    def info_fields(self) -> InfoFields:
        """Return the decoder and broker settings; never the broker password."""
        return (
            ("handler type", self.handler_type),
            ("broker host", self.broker_host),
            ("broker port", self.broker_port),
            ("broker username", self.broker_username or "(none)"),
            ("data source", self.analytics_data_source_key),
            ("device API protocol", self.device_api_protocol),
        )


@dataclass(frozen=True, slots=True)
class LiveWebSocketOverlaySourceSpec:
    """Description of Axis DataHub WebSocket analytics payloads."""

    handler_type: str
    topic: str
    channel_id: int = 1
    device_api_protocol: str = "https"
    source_kind: ClassVar[OverlaySourceKind] = OverlaySourceKind.WEBSOCKET_SOURCE
    source_location: ClassVar[None] = None

    def info_fields(self) -> InfoFields:
        """Return the decoder and DataHub subscription."""
        return (
            ("handler type", self.handler_type),
            ("topic", self.topic),
            ("channel ID", self.channel_id),
            ("device API protocol", self.device_api_protocol),
        )


LiveOverlaySourceSpec = LiveRTSPOverlaySourceSpec | LiveMQTTOverlaySourceSpec | LiveWebSocketOverlaySourceSpec
OverlaySourceSpec = FileOverlaySourceSpec | LiveOverlaySourceSpec


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverlayContent:
    """An overlay lane with a label, source spec, and plugin-defined metadata."""

    display_name: str
    source_spec: OverlaySourceSpec
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def source_kind(self) -> OverlaySourceKind:
        """Return the kind owned by this overlay's source specification."""
        return self.source_spec.source_kind


@dataclass(frozen=True, slots=True)
class LiveVideoContent:
    """A live workspace video with zero or more live overlay lanes.

    - 0 overlays: plain video playback
    - 1 overlay: video with overlay rendered on top
    Live playback supports at most one RTSP, MQTT, or DataHub WebSocket overlay source.
    """

    display_name: str
    source_spec: LiveRTSPStreamSpec
    overlays: tuple[OverlayContent, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    content_id: str = field(default_factory=_new_content_id)
    is_live: ClassVar[bool] = True

    def __post_init__(self) -> None:
        """Reject overlay shapes unsupported by live playback."""
        if len(self.overlays) > 1:
            raise ValueError("Live video content supports at most one overlay.")
        if any(not isinstance(overlay.source_spec, LiveOverlaySourceSpec) for overlay in self.overlays):
            raise TypeError(
                "Live video content requires an RTSP or MQTT overlay source spec; WebSocket overlays are supported too."
            )

    @property
    def source_location(self) -> str:
        """Return the device and camera head, shown to tell same-named streams apart."""
        return f"{self.source_spec.host}/camera head {self.source_spec.camera_head}"

    @property
    def overlay_spec(self) -> LiveOverlaySourceSpec | None:
        """Return the source spec of the live overlay, or None for plain video."""
        return cast(LiveOverlaySourceSpec, self.overlays[0].source_spec) if self.overlays else None

    def info_fields(self) -> InfoFields:
        """Return the content type, overlay count, and stream settings."""
        return (("type", "live video"), ("overlays", len(self.overlays)), *self.source_spec.info_fields())

    def standalone_lanes(self) -> tuple[EntryLane, ...]:
        """Return the visible lanes for this standalone live video item."""
        if self.overlays:
            return tuple(create_entry_lane(self, default_considered=True, overlay=overlay) for overlay in self.overlays)
        return (create_entry_lane(self, default_considered=True),)

    def consideration_items(self) -> tuple[ConsiderationItem, ...]:
        """Return no items because live overlay consideration is unsupported."""
        return ()

    def on_screen_item(self, entry_index: int = 0) -> OnScreenWorkspaceItem:
        """Return the workspace item a viewer shows for this video."""
        return OnScreenWorkspaceItem(kind="video", content_id=self.content_id)


@dataclass(frozen=True, slots=True)
class SeekableVideoContent:
    """A seekable workspace video with zero or more overlays."""

    display_name: str
    source_spec: FileVideoSourceSpec
    overlays: tuple[OverlayContent, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    content_id: str = field(default_factory=_new_content_id)
    is_live: ClassVar[bool] = False

    def __post_init__(self) -> None:
        """Reject overlay shapes unsupported by seekable playback."""
        if any(not isinstance(overlay.source_spec, FileOverlaySourceSpec) for overlay in self.overlays):
            raise TypeError("Seekable video content requires file overlay source specs.")

    @property
    def source_location(self) -> str:
        """Return the video file path, shown to tell same-named items apart."""
        return str(self.source_spec.path)

    def info_fields(self) -> InfoFields:
        """Return the content type, overlay count, and video file settings."""
        return (("type", "seekable video"), ("overlays", len(self.overlays)), *self.source_spec.info_fields())

    def standalone_lanes(self) -> tuple[EntryLane, ...]:
        """Return the visible lanes for this standalone seekable video item."""
        if not self.overlays:
            return (create_entry_lane(self, default_considered=True),)
        if len(self.overlays) == 1:
            return (create_entry_lane(self, default_considered=True, overlay=self.overlays[0]),)
        return tuple(create_entry_lane(self, default_considered=True, overlay=overlay) for overlay in self.overlays)

    def consideration_items(self) -> tuple[ConsiderationItem, ...]:
        """Return the standalone overlay lanes whose layout can be toggled."""
        return tuple(
            ConsiderationItem(
                ref=ConsiderationItemRef.video_lane(self.content_id, lane_index),
                default_considered=lane.default_considered,
            )
            for lane_index, lane in enumerate(self.standalone_lanes())
            if lane.source_kind != OverlaySourceKind.NO_SOURCE
        )

    @property
    def entries(self) -> tuple[PlaylistEntry, ...]:
        """Return this video as a one-entry playlist of its standalone lanes."""
        return (PlaylistEntry(lanes=self.standalone_lanes(), default_considered=True),)

    def entry_consideration_ref(self, entry_index: int) -> None:
        """Return None because the single entry of a standalone video is always considered."""
        return None

    def lane_consideration_ref(self, entry_index: int, lane_index: int) -> ConsiderationItemRef:
        """Return the reference that toggles one standalone overlay lane."""
        return ConsiderationItemRef.video_lane(self.content_id, lane_index)

    def on_screen_item(self, entry_index: int = 0) -> OnScreenWorkspaceItem:
        """Return the workspace item a viewer shows for this video."""
        return OnScreenWorkspaceItem(kind="video", content_id=self.content_id)


@dataclass(frozen=True, slots=True)
class EntryLane:
    """One visible comparison lane within a playlist entry."""

    display_name: str
    video: SeekableVideoContent | LiveVideoContent
    default_considered: bool
    overlay: OverlayContent | None = None
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        """Reject overlay sources incompatible with the lane's video workflow."""
        if self.overlay is None:
            return
        if isinstance(self.video, SeekableVideoContent) and not isinstance(
            self.overlay.source_spec, FileOverlaySourceSpec
        ):
            raise TypeError("Seekable video lanes require file overlay source specs.")
        if isinstance(self.video, LiveVideoContent) and not isinstance(self.overlay.source_spec, LiveOverlaySourceSpec):
            raise TypeError(
                "Live video lanes require an RTSP or MQTT overlay source spec; WebSocket overlays are supported too."
            )

    @property
    def source_kind(self) -> OverlaySourceKind:
        """Return the overlay source kind, or no-source for a plain video lane."""
        return self.overlay.source_kind if self.overlay is not None else OverlaySourceKind.NO_SOURCE

    @property
    def source_location(self) -> str:
        """Return the overlay file location, or the video location for plain and live lanes."""
        if self.overlay is not None and self.overlay.source_spec.source_location is not None:
            return self.overlay.source_spec.source_location
        return self.video.source_location


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    """One step in a playlist — everything shown simultaneously.

    Each lane is one visible panel. Multiple lanes may share the same video
    but differ by overlay, or may refer to distinct videos.
    """

    lanes: tuple[EntryLane, ...]
    default_considered: bool
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        """Reject unsupported empty or live playlist lanes before they enter the Workspace."""
        if not self.lanes:
            raise ValueError("Playlist entries require at least one lane.")
        if any(not isinstance(lane.video, SeekableVideoContent) for lane in self.lanes):
            raise TypeError("Playlist entries require seekable video content.")

    def display_label(self, entry_index: int) -> str:
        """Return the default label for this entry in workspace views."""
        unique_videos = {lane.video for lane in self.lanes}
        if len(unique_videos) == 1:
            return self.lanes[0].video.display_name
        if len(self.lanes) == 1:
            return self.lanes[0].display_name
        return f"Entry {entry_index + 1}"

    @property
    def source_location(self) -> str | None:
        """Return the entry's video location when all lanes show one video, otherwise None."""
        unique_videos = {lane.video for lane in self.lanes}
        if len(unique_videos) == 1:
            return self.lanes[0].video.source_location
        return None

    def should_show_lane_children(self) -> bool:
        """Return whether UI consumers should render child lanes for this entry."""
        return len(self.lanes) > 1 or any(lane.source_kind != OverlaySourceKind.NO_SOURCE for lane in self.lanes)


@dataclass(frozen=True, slots=True)
class PlaylistContent:
    """An ordered sequence of entries to step through."""

    display_name: str
    entries: tuple[PlaylistEntry, ...]
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    content_id: str = field(default_factory=_new_content_id)

    def __post_init__(self) -> None:
        """Reject empty playlists before they enter the Workspace."""
        if not self.entries:
            raise ValueError("Playlists require at least one entry.")

    @property
    def source_location(self) -> None:
        """Return None because playlists are resolved from several sources."""
        return None

    def info_fields(self) -> InfoFields:
        """Return the content type and entry and lane counts."""
        lane_count = sum(len(entry.lanes) for entry in self.entries)
        return (("type", "playlist"), ("entries", len(self.entries)), ("lanes", lane_count))

    def consideration_items(self) -> tuple[ConsiderationItem, ...]:
        """Return every playlist entry and lane whose participation can be toggled."""
        items: list[ConsiderationItem] = []
        for entry_index, entry in enumerate(self.entries):
            items.append(
                ConsiderationItem(
                    ref=ConsiderationItemRef.playlist_entry(self.content_id, entry_index),
                    default_considered=entry.default_considered,
                )
            )
            if entry.should_show_lane_children():
                items.extend(
                    ConsiderationItem(
                        ref=ConsiderationItemRef.playlist_lane(self.content_id, entry_index, lane_index),
                        default_considered=lane.default_considered,
                    )
                    for lane_index, lane in enumerate(entry.lanes)
                )
        return tuple(items)

    def entry_consideration_ref(self, entry_index: int) -> ConsiderationItemRef:
        """Return the reference that toggles one playlist entry."""
        return ConsiderationItemRef.playlist_entry(self.content_id, entry_index)

    def lane_consideration_ref(self, entry_index: int, lane_index: int) -> ConsiderationItemRef:
        """Return the reference that toggles one lane of a playlist entry."""
        return ConsiderationItemRef.playlist_lane(self.content_id, entry_index, lane_index)

    def on_screen_item(self, entry_index: int = 0) -> OnScreenWorkspaceItem:
        """Return the workspace item a viewer shows for one playlist entry."""
        return OnScreenWorkspaceItem(kind="playlist_entry", content_id=self.content_id, entry_index=entry_index)


Content = SeekableVideoContent | LiveVideoContent | PlaylistContent


def create_entry_lane(
    video: SeekableVideoContent | LiveVideoContent,
    *,
    default_considered: bool,
    display_name: str | None = None,
    overlay: OverlayContent | None = None,
    metadata: dict[str, Any] | None = None,
) -> EntryLane:
    """Build one visible lane with consistent label, kind, and metadata defaults."""
    return EntryLane(
        display_name=display_name or (overlay.display_name if overlay is not None else video.display_name),
        video=video,
        default_considered=default_considered,
        overlay=overlay,
        metadata=metadata if metadata is not None else {},
    )
