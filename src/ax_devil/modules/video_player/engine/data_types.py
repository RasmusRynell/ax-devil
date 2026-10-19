"""Video frame display data types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from PySide6.QtGui import QImage

from .quick.preparation import DrawingBuffer
from .quick.prepared import PreparedDrawing
from .render_context import RenderContext

DrawingGenerator = Callable[[RenderContext, DrawingBuffer], PreparedDrawing]


@dataclass(frozen=True, slots=True)
class VideoFrame:
    """Video frame data for display.

    Contains the image data and basic metadata needed for video rendering.
    """

    image: QImage
    timestamp_us: int  # Frame time on the media clock, in microseconds
    frame_id: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class HoverHit:
    """Result of hit-testing an interactive region in normalized coordinates."""

    target_id: str
    bounds: tuple[float, float, float, float]  # (x, y, width, height) normalized [0,1]
    label: str  # Identity of the target, such as "12 person"
    detail: str  # Facts about the target in one line, such as "conf 0.97 · speed 1.2"; empty when there are none
    color_key: str  # Names the category whose color marks the target


class HoverInteractionProvider(Protocol):
    """Domain adapter interface for hover interactions in the video renderer."""

    def hit_test(self, nx: float, ny: float) -> HoverHit | None:
        """Return hover hit for a normalized point, or None if no interactive target exists."""

    def get_hit_by_id(self, target_id: str) -> HoverHit | None:
        """Return hover hit metadata for an existing target id, or None if it no longer exists."""


@dataclass(frozen=True, slots=True)
class VideoOverlayData:
    """Video overlay data for rendering on top of video frames.

    The generator owns its source and prepares final output for the exact drawing settings.
    """

    drawing_generator: DrawingGenerator
    timestamp_us: int  # Overlay time on the media clock, in microseconds
    overlay_id: int | None = None
    metadata: dict[str, Any] | None = None
    interaction_provider: HoverInteractionProvider | None = None


@dataclass(frozen=True, slots=True)
class VideoFrameWithOverlays:
    """Combined video frame and overlay data ready for display.

    This represents a complete displayable video frame with annotations.
    """

    frame: VideoFrame
    overlays: VideoOverlayData | None

    def prepare_overlays(self, context: RenderContext, buffer: DrawingBuffer) -> tuple[PreparedDrawing | None, float]:
        """Retrieve prepared drawing data and resolve their clamped opacity."""
        overlay = self.overlays
        if overlay is None:
            return None, 1.0
        drawing = overlay.drawing_generator(context, buffer)
        try:
            opacity = float((overlay.metadata or {}).get("overlay_opacity", 1.0))
        except (TypeError, ValueError):
            opacity = 1.0
        return drawing, max(0.0, min(1.0, opacity))
