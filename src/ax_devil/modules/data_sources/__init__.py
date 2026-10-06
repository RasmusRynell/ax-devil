"""Data Sources Package.

Clean, lean video and overlay sources for the synchronized video viewer. Supports file playback, RTSP streams, and
synthetic test data.
"""

from .base import (
    DataSource,
    FrameSource,
    OverlayLookup,
    OverlaySource,
    SeekableFrameSource,
)
from .file_frame_source import FileFrameSource
from .file_overlay_source import FileOverlaySource
from .live.mqtt_overlay_source import MQTTOverlaySource
from .live.rtsp_source import RTSPOverlayDecoder, RTSPSource
from .live.websocket_overlay_source import WebSocketOverlaySource

__all__ = [
    # Base interfaces
    "DataSource",
    "FrameSource",
    "OverlayLookup",
    "OverlaySource",
    "SeekableFrameSource",
    # Frame sources
    "FileFrameSource",
    "RTSPSource",
    "RTSPOverlayDecoder",
    # Overlay sources
    "FileOverlaySource",
    "MQTTOverlaySource",
    "WebSocketOverlaySource",
]
