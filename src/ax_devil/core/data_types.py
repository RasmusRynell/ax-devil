"""Core frame identity and timestamped frame/overlay packets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from ax_devil.modules.scene.model import Scene


@dataclass(frozen=True, slots=True)
class FrameIdentifier:
    """Frame identifier containing both sequence ID and media timestamp.

    ``timestamp_us`` is integer microseconds on the media clock, a Scene datetime's time since the Unix epoch.
    Live frames carry capture time; an offline video's first frame is 0. Overlays use the same clock:
    nothing is shifted anywhere.
    """

    sequence_id: int  # Frame index (0, 1, 2, ...)
    timestamp_us: int


@dataclass(frozen=True, slots=True)
class TimestampedData:
    """Base class for timestamped data with proper timestamp semantics."""

    frame_id: FrameIdentifier  # Frame identifier with sequence_id and media timestamp
    content: Any  # The actual data content
    source_id: str = "Unknown"
    metadata: dict[str, Any] | None = None


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
