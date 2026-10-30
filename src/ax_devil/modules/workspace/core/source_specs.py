"""Overlay source kinds and the descriptions of file and live video and overlay sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, cast

from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import ImageSequenceConfig


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


InfoFields = tuple[tuple[str, object], ...]
"""Labeled values shown in the item information dialog, formatted by the dialog's payload builder."""


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
    decoder_kwargs: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def source_location(self) -> str:
        """Return the overlay file path for source labels and tooltips."""
        return str(self.path)

    @property
    def source_paths(self) -> SourcePaths:
        """Return the overlay file, which changing makes the overlay out of date."""
        return SourcePaths(files=frozenset({self.path}))

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
    device_api_protocol: DeviceProtocol = DeviceProtocol.HTTPS
    device_port: int = 0
    rtsp_port: int = 554

    def connection(self, timeout_s: float) -> DeviceConnection:
        """Return how to reach and sign in to the device's web API, giving up after *timeout_s* seconds."""
        return DeviceConnection(
            self.host,
            self.device_api_protocol,
            self.device_port,
            self.username,
            self.password,
            timeout_s,
        )

    def info_fields(self) -> InfoFields:
        """Return the device, camera head, and stream settings; never the password."""
        return (
            ("host", self.host),
            ("username", self.username),
            ("camera head", self.camera_head),
            ("resolution", self.resolution),
            ("stream URL", self.stream_url or "(default)"),
            ("device API protocol", self.device_api_protocol.name),
            ("device port", self.device_port or "(standard)"),
            ("RTSP port", self.rtsp_port),
        )


@dataclass(frozen=True, slots=True)
class LiveRTSPOverlaySourceSpec:
    """Description of embedded RTSP analytics payloads and decoder selection."""

    handler_type: str
    source_kind: ClassVar[OverlaySourceKind] = OverlaySourceKind.RTSP_SOURCE
    source_location: ClassVar[None] = None
    source_paths: ClassVar[SourcePaths] = SourcePaths()

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
    source_kind: ClassVar[OverlaySourceKind] = OverlaySourceKind.MQTT_SOURCE
    source_location: ClassVar[None] = None
    source_paths: ClassVar[SourcePaths] = SourcePaths()

    def info_fields(self) -> InfoFields:
        """Return the decoder and broker settings; never the broker password."""
        return (
            ("handler type", self.handler_type),
            ("broker host", self.broker_host),
            ("broker port", self.broker_port),
            ("broker username", self.broker_username or "(none)"),
            ("data source", self.analytics_data_source_key),
        )


@dataclass(frozen=True, slots=True)
class LiveWebSocketOverlaySourceSpec:
    """Description of Axis DataHub WebSocket analytics payloads."""

    handler_type: str
    topic: str
    channel_id: int = 1
    source_kind: ClassVar[OverlaySourceKind] = OverlaySourceKind.WEBSOCKET_SOURCE
    source_location: ClassVar[None] = None
    source_paths: ClassVar[SourcePaths] = SourcePaths()

    def info_fields(self) -> InfoFields:
        """Return the decoder and DataHub subscription."""
        return (
            ("handler type", self.handler_type),
            ("topic", self.topic),
            ("channel ID", self.channel_id),
        )


LiveOverlaySourceSpec = LiveRTSPOverlaySourceSpec | LiveMQTTOverlaySourceSpec | LiveWebSocketOverlaySourceSpec
OverlaySourceSpec = FileOverlaySourceSpec | LiveOverlaySourceSpec
