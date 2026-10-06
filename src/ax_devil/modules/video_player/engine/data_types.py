"""Video frame display data types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol

from PySide6.QtGui import QImage

from .quick.preparation import DrawingBuffer, PreparedDrawing
from .render_context import RenderContext

DrawingGenerator = Callable[[RenderContext, DrawingBuffer], PreparedDrawing]


@dataclass(frozen=True, slots=True)
class VideoFrame:
    """Video frame data for display.

    Contains the image data and basic metadata needed for video rendering.
    """

    image: QImage
    timestamp: float  # Frame timestamp for synchronization
    frame_id: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None
    timestamp_monotime_us: float | None = None


@dataclass(frozen=True, slots=True)
class HoverHit:
    """Result of hit-testing an interactive region in normalized coordinates."""

    target_id: str
    bounds: tuple[float, float, float, float]  # (x, y, width, height) normalized [0,1]
    card_html: str


class HoverInteractionProvider(Protocol):
    """Domain adapter interface for hover interactions in the video renderer."""

    def hit_test(self, nx: float, ny: float) -> HoverHit | None:
        """Return hover hit for a normalized point, or None if no interactive target exists."""

    def get_hit_by_id(self, target_id: str) -> HoverHit | None:
        """Return hover hit metadata for an existing target id, or None if it no longer exists."""


class DrawingBuildReason(str, Enum):
    """Inputs that require rebuilding the cached prepared drawing."""

    INITIAL = "First build"
    FILTER = "Filter changed"
    TARGET = "Drawing target changed"
    CATALOG = "Catalog changed"


@dataclass(frozen=True, slots=True)
class DrawingPreparationMetrics:
    """Catalog and final-backend preparation metrics for one overlay."""

    filter_time_ms: float | None = None
    generation_time_ms: float | None = None
    filter_cache_hit: bool = False
    drawing_cache_hit: bool = False
    build_reasons: tuple[DrawingBuildReason, ...] = ()
    input_entity_count: int = 0
    filtered_entity_count: int = 0
    primitive_counts: tuple[tuple[str, int], ...] = ()


class DrawingPreparationMetricsProvider(Protocol):
    """Provider for the latest drawing preparation metrics."""

    def latest_drawing_preparation_metrics(self) -> DrawingPreparationMetrics:
        """Return metrics from the most recent drawing preparation."""


@dataclass(frozen=True, slots=True)
class VideoOverlayData:
    """Video overlay data for rendering on top of video frames.

    The generator owns its source and prepares final output for the exact drawing settings.
    """

    drawing_generator: DrawingGenerator
    timestamp: float  # Overlay timestamp for synchronization
    overlay_id: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None
    interaction_provider: HoverInteractionProvider | None = None
    metrics_provider: DrawingPreparationMetricsProvider | None = None
    timestamp_monotime_us: float | None = None


@dataclass(frozen=True, slots=True)
class VideoFrameWithOverlays:
    """Combined video frame and overlay data ready for display.

    This represents a complete displayable video frame with annotations.
    """

    frame: VideoFrame
    overlays: Optional[VideoOverlayData]

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
