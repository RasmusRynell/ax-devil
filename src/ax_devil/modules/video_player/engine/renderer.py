"""Video frame rendering engine for high-performance playback."""

from __future__ import annotations

from PySide6 import QtCore
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCursor, QHideEvent, QMouseEvent, QResizeEvent, QShowEvent, QWheelEvent
from PySide6.QtWidgets import QWidget

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings

from ..ui.hover_chip import HoverChip
from .data_types import (
    HoverHit,
    HoverInteractionProvider,
    VideoFrameWithOverlays,
)
from .quick.surface import Bounds, QuickSurface
from .render_context import RenderContext
from .viewport_state import NormalizedViewport, ViewportState, ZoomStep, fit_frame_rect

logger = get_logger(__name__)

_ZOOM_MIN = 1.0
_ZOOM_MAX = 10.0
_ZOOM_STEP = 1.15


class VideoFrameRenderer(QWidget):
    """Viewport interaction and coalesced Quick frame preparation."""

    viewportChanged = Signal(object)  # NormalizedViewport
    selectionChanged = Signal(object)  # Id of the object the user selected by clicking it, or None when cleared

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._logger = logger
        self.setMouseTracking(True)
        self._video_frame: VideoFrameWithOverlays | None = None
        self._viewport = ViewportState(
            zoom_level=_ZOOM_MIN,
            zoom_min=_ZOOM_MIN,
            zoom_max=_ZOOM_MAX,
            zoom_step=_ZOOM_STEP,
        )
        self._cached_render_context: RenderContext | None = None
        self._hover_hit: HoverHit | None = None
        self._selected_id: str | None = None
        self._selected_bounds: Bounds | None = None
        self._did_pan_during_drag = False
        self._stable_viewport: NormalizedViewport | None = None
        self._quick = QuickSurface(self)
        self._quick.setGeometry(self.rect())
        self._quick.hide()
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setSingleShot(True)
        self._prepare_timer.timeout.connect(self._prepare_quick_frame)
        self._chip = HoverChip(self)

        GlobalSettings().overlay_preference_changed.connect(self._on_overlay_preference_changed)

    @Slot(VideoFrameWithOverlays)
    def display_frame(self, video_frame: VideoFrameWithOverlays) -> None:
        self._video_frame = video_frame
        self.refresh_last_frame()

    def clear(self) -> None:
        self._set_selected(None)
        self._clear_hover()
        self._video_frame = None
        self._stable_viewport = None
        self._prepare_timer.stop()
        self._quick.clear()
        self._quick.hide()
        self._request_repaint()

    def refresh_last_frame(self) -> None:
        if self.isVisible() and self._video_frame is not None:
            self._refresh_selected_bounds()
            self._refresh_hover_from_cursor()
            self._request_repaint()

    def _request_repaint(self) -> None:
        if not self.isVisible():
            return
        if self._video_frame is None:
            self.update()
            return
        if not self._prepare_timer.isActive():
            self._prepare_timer.start(0)

    def _prepare_quick_frame(self) -> None:
        self._prepare_timer.stop()
        surface, frame = self._quick, self._video_frame
        if not self.isVisible() or frame is None or self.width() <= 0 or self.height() <= 0:
            return
        target = self._aspect_respected_rect(frame.frame.image.size())
        if target.width() < 1 or target.height() < 1:
            return
        context = self._create_render_context(target)
        surface.set_frame_image(frame.frame.image, target)
        drawing, opacity = frame.prepare_overlays(context, surface.drawing_buffer(target, context.scale_factor))
        surface.set_overlays(
            drawing,
            opacity,
            target,
            context.scale_factor,
            self._hover_hit.bounds if self._hover_hit is not None else None,
            self._selected_bounds,
        )
        if surface.isHidden():
            surface.show()
            surface.lower()

    def cleanup(self) -> None:
        """Release the submitted frame and native resources."""
        self.clear()
        self._prepare_timer.stop()
        self._quick.cleanup()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        if self._video_frame is not None:
            self._stable_viewport = self._viewport.follow_resize(
                self._stable_viewport, self._video_frame.frame.image.size(), event.oldSize(), event.size()
            )
        super().resizeEvent(event)
        self._quick.setGeometry(self.rect())
        self._clear_hover()
        self._request_repaint()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Catch up with the latest frame after this viewport or an ancestor was hidden."""
        super().showEvent(event)
        self.refresh_last_frame()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        """Discard queued drawing work; frame delivery still retains the latest snapshot."""
        super().hideEvent(event)
        self._clear_hover()
        self._prepare_timer.stop()

    def _aspect_respected_rect(self, img_size: QtCore.QSize) -> QtCore.QRectF:
        base = self._base_rect(img_size)
        return self._viewport.compute_target_rect(base)

    def frame_display_rect(self) -> QtCore.QRectF | None:
        """Return the unzoomed frame rectangle fitted within the renderer."""
        if self._video_frame is None:
            return None
        return self._base_rect(self._video_frame.frame.image.size())

    def visible_frame_rect(self) -> QtCore.QRectF | None:
        """Return the part of the zoomed and panned frame that is on screen, or None if none is."""
        if self._video_frame is None:
            return None
        visible = self._aspect_respected_rect(self._video_frame.frame.image.size()).intersected(
            QtCore.QRectF(self.rect())
        )
        return None if visible.isEmpty() else visible

    def _base_rect(self, img_size: QtCore.QSize) -> QtCore.QRectF:
        return fit_frame_rect(img_size, self.rect())

    def _store_stable_viewport(self, base: QtCore.QRectF) -> None:
        if self._video_frame is not None:
            self._stable_viewport = self._viewport.to_normalized(base)

    def _clamp_pan(self, base: QtCore.QRectF) -> None:
        self._viewport.clamp_pan(base, QtCore.QRectF(self.rect()))

    def _emit_viewport_changed(self) -> None:
        """Emit normalized viewport state for cross-player synchronization."""
        if self._video_frame is None:
            return
        base = self._base_rect(self._video_frame.frame.image.size())
        self.viewportChanged.emit(self._viewport.to_normalized(base))

    def set_viewport(self, viewport: NormalizedViewport) -> None:
        """Apply viewport state from a sibling renderer. Does not emit viewportChanged."""
        if self._video_frame is not None:
            base = self._base_rect(self._video_frame.frame.image.size())
            self._viewport.apply_normalized(viewport, base, QtCore.QRectF(self.rect()))
            self._stable_viewport = viewport
        else:
            self._viewport.zoom_level = max(self._viewport.zoom_min, min(viewport.zoom, self._viewport.zoom_max))
            self._viewport.pan_offset = QtCore.QPointF(0.0, 0.0)
            self._stable_viewport = None
        if self._viewport.reset_if_unzoomed():
            self._stable_viewport = None
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._request_repaint()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._video_frame is None:
            super().wheelEvent(event)
            return

        self._zoom_toward(event.angleDelta().y(), event.position())
        event.accept()

    def zoom(self, step: ZoomStep) -> None:
        """Apply one keyboard zoom step around the center of the view."""
        if self._video_frame is None:
            return
        base = self._base_rect(self._video_frame.frame.image.size())
        self._viewport.step_zoom(step, QtCore.QRectF(self.rect()).center(), base)
        self._apply_zoom_change(base)

    def _zoom_toward(self, delta_y: int, anchor: QtCore.QPointF) -> None:
        if self._video_frame is None:
            return
        base = self._base_rect(self._video_frame.frame.image.size())
        self._viewport.zoom_toward(delta_y, anchor, base)
        self._apply_zoom_change(base)

    def _apply_zoom_change(self, base: QtCore.QRectF) -> None:
        self._clear_hover()

        if self._viewport.reset_if_unzoomed():
            self._stable_viewport = None
            self.unsetCursor()
        else:
            self._clamp_pan(base)
            self._store_stable_viewport(base)
            self.setCursor(Qt.CursorShape.OpenHandCursor)

        self._request_repaint()
        self._emit_viewport_changed()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._did_pan_during_drag = False

        if event.button() == Qt.MouseButton.LeftButton and self._viewport.zoom_level > _ZOOM_MIN:
            self._viewport.begin_pan(event.position())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._viewport.is_panning and self._video_frame is not None:
            self._did_pan_during_drag = True
            base = self._base_rect(self._video_frame.frame.image.size())
            self._viewport.update_pan(event.position(), base, QtCore.QRectF(self.rect()))
            self._store_stable_viewport(base)
            self._clear_hover()
            self._request_repaint()
            self._emit_viewport_changed()
            event.accept()
        else:
            self._update_hover(event)
            super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802
        self._clear_hover()
        super().leaveEvent(event)

    def select_target(self, target_id: str | None) -> None:
        """Select the object *target_id*, or clear the selection with None; does not emit ``selectionChanged``.

        An id that is not in the displayed frame stays selected and gets its box when it appears.
        """
        self._set_selected(target_id, emit=False)

    def _handle_click_selection(self, pos: QtCore.QPointF) -> bool:
        """Select the object under a click, or clear the selection on empty video. Returns True in the video area."""
        target = self._hover_target_rect()
        if target is None or not target.contains(pos):
            return False

        nx = (pos.x() - target.x()) / target.width()
        ny = (pos.y() - target.y()) / target.height()

        hit = self._hit_test(nx, ny)
        self._set_selected(hit.target_id if hit is not None else None)
        return True

    def _set_selected(self, target_id: str | None, *, emit: bool = True) -> None:
        if target_id == self._selected_id:
            return
        self._selected_id = target_id
        self._refresh_selected_bounds()
        self._request_repaint()
        if emit:
            self.selectionChanged.emit(target_id)

    def _refresh_selected_bounds(self) -> None:
        """Follow the selected object in the displayed frame; it has no box while it is not in the frame."""
        if not self.isVisible():
            self._selected_bounds = None
            return
        hit = None
        provider = self._interaction_provider()
        if self._selected_id is not None and provider is not None:
            hit = provider.get_hit_by_id(self._selected_id)
        self._selected_bounds = hit.bounds if hit is not None else None

    def _update_hover(self, event: QMouseEvent) -> None:
        """Hit-test entities at the cursor position and update the hover chip."""
        self._update_hover_at_position(event.position())

    def _refresh_hover_from_cursor(self) -> None:
        """Refresh hover against latest frame using the current cursor position."""
        if not self.underMouse():
            self._clear_hover()
            return

        cursor_pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(cursor_pos):
            self._clear_hover()
            return

        self._update_hover_at_position(QtCore.QPointF(cursor_pos))

    def _update_hover_at_position(self, pos: QtCore.QPointF) -> None:
        """Hit-test entities at a widget-space cursor position and update hover UI."""
        target = self._hover_target_rect()
        if target is None:
            self._clear_hover()
            return

        if not target.contains(pos):
            self._clear_hover()
            return

        nx = (pos.x() - target.x()) / target.width()
        ny = (pos.y() - target.y()) / target.height()

        hit = self._hit_test(nx, ny)
        if self._update_hover_hit(hit):
            self._request_repaint()  # Repaint to show/hide highlight
        self._chip.show_over(hit, target)

    def _hover_target_rect(self) -> QtCore.QRectF | None:
        if self._video_frame is None or self._video_frame.overlays is None:
            return None
        return self._aspect_respected_rect(self._video_frame.frame.image.size())

    def _update_hover_hit(self, hit: HoverHit | None) -> bool:
        """Retain the hit and rebuild the highlight only when its geometry changes."""
        previous = self._hover_hit
        self._hover_hit = hit
        return (hit.bounds if hit else None) != (previous.bounds if previous else None)

    def _clear_hover(self) -> None:
        if self._hover_hit is not None:
            self._hover_hit = None
            self._request_repaint()
        self._chip.hide_chip()

    def _on_overlay_preference_changed(self, preference: OverlayPreference, enabled: bool) -> None:
        """Drop the hover chip and the selection when object interaction is turned off; box a kept id when it is on."""
        if preference is not OverlayPreference.HOVER:
            return
        if enabled:
            self._refresh_selected_bounds()
        else:
            self._clear_hover()
            self._set_selected(None)
        self._request_repaint()

    def _hit_test(self, nx: float, ny: float) -> HoverHit | None:
        """Return hover hit for normalized point, if an interaction provider is available."""
        interaction_provider = self._interaction_provider()
        if interaction_provider is None:
            return None

        return interaction_provider.hit_test(nx, ny)

    def _interaction_provider(self) -> HoverInteractionProvider | None:
        if not GlobalSettings().is_overlay_enabled(OverlayPreference.HOVER):
            return None
        if self._video_frame is None or self._video_frame.overlays is None:
            return None
        return self._video_frame.overlays.interaction_provider

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        click_handled = False
        if event.button() == Qt.MouseButton.LeftButton and not self._did_pan_during_drag:
            click_handled = self._handle_click_selection(event.position())

        if self._viewport.is_panning:
            self._viewport.end_pan()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
        elif click_handled:
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _create_render_context(self, target: QtCore.QRectF) -> RenderContext:
        size = (int(target.width()), int(target.height()))
        if self._cached_render_context is not None and size == (
            self._cached_render_context.width,
            self._cached_render_context.height,
        ):
            return self._cached_render_context

        context = RenderContext.create(width=size[0], height=size[1])
        self._cached_render_context = context
        return context

    @property
    def viewport_state(self) -> ViewportState:
        """Expose viewport interaction state for orchestration and tests."""
        return self._viewport
