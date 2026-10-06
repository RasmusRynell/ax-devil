"""Viewport interaction state for video rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from PySide6.QtCore import QPointF, QRectF


class NormalizedViewport(NamedTuple):
    """Size-independent viewport snapshot for cross-player synchronization."""

    zoom: float
    pan_x: float
    pan_y: float


class ZoomStep(Enum):
    """One keyboard zoom request; the value is the wheel direction it steps in."""

    IN = 1
    OUT = -1
    RESET = 0


@dataclass(slots=True)
class ViewportState:
    """Owns zoom/pan state and movement logic for the rendered viewport."""

    zoom_level: float
    zoom_min: float
    zoom_max: float
    zoom_step: float
    pan_offset: QPointF = field(default_factory=lambda: QPointF(0.0, 0.0))
    is_panning: bool = False
    pan_start: QPointF = field(default_factory=lambda: QPointF(0.0, 0.0))

    def compute_target_rect(self, base: QRectF) -> QRectF:
        """Return the rendered target rect for the current zoom/pan state."""
        if self.zoom_level <= self.zoom_min:
            return base

        cx, cy = base.center().x(), base.center().y()
        zoomed_w = base.width() * self.zoom_level
        zoomed_h = base.height() * self.zoom_level
        return QRectF(
            cx - zoomed_w / 2.0 + self.pan_offset.x(),
            cy - zoomed_h / 2.0 + self.pan_offset.y(),
            zoomed_w,
            zoomed_h,
        )

    def apply_wheel_delta(self, delta_y: int) -> None:
        """Update zoom level from wheel delta while respecting bounds."""
        if delta_y > 0:
            self.zoom_level = min(self.zoom_level * self.zoom_step, self.zoom_max)
        else:
            self.zoom_level = max(self.zoom_level / self.zoom_step, self.zoom_min)

    def zoom_toward(self, delta_y: int, anchor: QPointF, base: QRectF) -> None:
        """Zoom in/out keeping the content under *anchor* visually fixed."""
        old_zoom = self.zoom_level
        self.apply_wheel_delta(delta_y)
        if self.zoom_level == old_zoom:
            return
        ratio = self.zoom_level / old_zoom
        cx, cy = base.center().x(), base.center().y()
        self.pan_offset = QPointF(
            (anchor.x() - cx) * (1.0 - ratio) + self.pan_offset.x() * ratio,
            (anchor.y() - cy) * (1.0 - ratio) + self.pan_offset.y() * ratio,
        )

    def step_zoom(self, step: ZoomStep, anchor: QPointF, base: QRectF) -> None:
        """Apply one keyboard zoom *step* around *anchor*; reset returns to the fitted view."""
        if step is ZoomStep.RESET:
            self.zoom_level = self.zoom_min
        else:
            self.zoom_toward(step.value, anchor, base)

    def begin_pan(self, position: QPointF) -> None:
        """Start panning from the given cursor position."""
        self.is_panning = True
        self.pan_start = position

    def update_pan(self, position: QPointF, base: QRectF, widget_rect: QRectF) -> None:
        """Update pan offset based on cursor movement and clamp to valid bounds."""
        delta = position - self.pan_start
        self.pan_offset = QPointF(self.pan_offset.x() + delta.x(), self.pan_offset.y() + delta.y())
        self.pan_start = position
        self.clamp_pan(base, widget_rect)

    def end_pan(self) -> None:
        """Stop panning."""
        self.is_panning = False

    def reset_if_unzoomed(self) -> bool:
        """Reset pan when zoom is at minimum. Returns True when reset happened."""
        if self.zoom_level > self.zoom_min:
            return False
        self.zoom_level = self.zoom_min
        self.pan_offset = QPointF(0.0, 0.0)
        return True

    def clamp_pan(self, base: QRectF, widget_rect: QRectF) -> None:
        """Clamp pan so zoomed content stays within viewport constraints."""
        if self.zoom_level <= self.zoom_min:
            self.pan_offset = QPointF(0.0, 0.0)
            return

        zoomed_w = base.width() * self.zoom_level
        zoomed_h = base.height() * self.zoom_level
        cx, cy = base.center().x(), base.center().y()

        if zoomed_w <= widget_rect.width():
            px = 0.0
        else:
            min_px = widget_rect.right() - (cx + zoomed_w / 2.0)
            max_px = widget_rect.left() - (cx - zoomed_w / 2.0)
            px = max(min_px, min(max_px, self.pan_offset.x()))

        if zoomed_h <= widget_rect.height():
            py = 0.0
        else:
            min_py = widget_rect.bottom() - (cy + zoomed_h / 2.0)
            max_py = widget_rect.top() - (cy - zoomed_h / 2.0)
            py = max(min_py, min(max_py, self.pan_offset.y()))

        self.pan_offset = QPointF(px, py)

    def normalized_pan(self, base: QRectF) -> tuple[float, float]:
        """Return pan offset as fractions of zoomed content size."""
        zoomed_w = base.width() * self.zoom_level
        zoomed_h = base.height() * self.zoom_level
        norm_x = self.pan_offset.x() / zoomed_w if zoomed_w > 0 else 0.0
        norm_y = self.pan_offset.y() / zoomed_h if zoomed_h > 0 else 0.0
        return norm_x, norm_y

    def to_normalized(self, base: QRectF) -> NormalizedViewport:
        """Return a size-independent snapshot of the current viewport."""
        pan_x, pan_y = self.normalized_pan(base)
        return NormalizedViewport(zoom=self.zoom_level, pan_x=pan_x, pan_y=pan_y)

    def apply_normalized_pan(self, norm_x: float, norm_y: float, base: QRectF, widget_rect: QRectF) -> None:
        """Set pan offset from normalized fractions and clamp to valid bounds."""
        zoomed_w = base.width() * self.zoom_level
        zoomed_h = base.height() * self.zoom_level
        self.pan_offset = QPointF(norm_x * zoomed_w, norm_y * zoomed_h)
        self.clamp_pan(base, widget_rect)

    def apply_normalized(self, viewport: NormalizedViewport, base: QRectF, widget_rect: QRectF) -> None:
        """Apply a full normalized viewport snapshot (zoom + pan), clamped to valid bounds."""
        self.zoom_level = max(self.zoom_min, min(viewport.zoom, self.zoom_max))
        self.apply_normalized_pan(viewport.pan_x, viewport.pan_y, base, widget_rect)
