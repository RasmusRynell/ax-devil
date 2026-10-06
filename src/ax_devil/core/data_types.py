"""Core frame identity and timestamped frame/overlay packets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from ax_devil.modules.scene.model import Scene


@dataclass(frozen=True, slots=True)
class FrameIdentifier:
    """Frame identifier containing both sequence ID and monotime timestamp.

    Provides dual identification for frames: sequence_id (frame index) and
    timestamp_monotime_us (microseconds since first frame). This enables
    overlay sources to use either identifier for lookup.
    """

    sequence_id: int  # Frame index (0, 1, 2, ...)
    timestamp_monotime_us: float  # Timestamp represented as microseconds since:
    # * start of video if offline
    # * since 1970-01-01 if live stream)


@dataclass(frozen=True, slots=True)
class TimestampedData:
    """Base class for timestamped data with proper timestamp semantics."""

    frame_id: FrameIdentifier  # Frame identifier with sequence_id and monotime
    content: Any  # The actual data content
    source_id: str = "Unknown"
    metadata: Optional[dict[str, Any]] = None


@dataclass(frozen=True, slots=True)
class FrameData(TimestampedData):
    """Timestamped video frame data with proper timestamp semantics."""

    content: QImage  # Specialized type hint for content


@dataclass(frozen=True, slots=True)
class OverlayData(TimestampedData):
    """Timestamped overlay data with proper timestamp semantics.

    Contains a Scene (world model) that will be converted to visual overlays
    at the display layer for filtering and rendering.
    """

    content: "Scene"  # Specialized type hint for content
