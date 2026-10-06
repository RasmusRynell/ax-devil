"""Video frame rendering engine for high-performance playback."""

from __future__ import annotations

from itertools import count
from time import perf_counter
from typing import Optional

from PySide6 import QtCore
from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCursor, QMouseEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QWidget

from ax_devil.modules.diagnostics.metrics_gate import is_metrics_enabled
from ax_devil.modules.diagnostics.render_metrics import FrameIdentity, PaintSample, get_render_metrics_store
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_player.engine.video_transforms import calculate_aspect_preserving_fit

from ..ui.entity_hover_card import EntityHoverCard
from .data_types import (
    HoverHit,
    HoverInteractionProvider,
    VideoFrameWithOverlays,
)
from .quick.surface import QuickSurface
from .render_context import RenderContext
from .viewport_state import NormalizedViewport, ViewportState, ZoomStep

logger = get_logger(__name__)

_VIEWER_IDS = count(1)

_ZOOM_MIN = 1.0
_ZOOM_MAX = 10.0
_ZOOM_STEP = 1.15


class VideoFrameRenderer(QWidget):
    """Viewport interaction, coalesced Quick frame preparation, and display diagnostics."""

    viewportChanged = Signal(object)  # NormalizedViewport

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._logger = logger
        self._metrics_instance_id = f"viewer-{next(_VIEWER_IDS)}"
        get_render_metrics_store().register(
            self._metrics_instance_id, self._metrics_instance_id.replace("-", " ").title()
        )

        self.setMinimumSize(QSize(100, 100))
        self.setMouseTracking(True)
        self._video_frame: Optional[VideoFrameWithOverlays] = None
        self._viewport = ViewportState(
            zoom_level=_ZOOM_MIN,
            zoom_min=_ZOOM_MIN,
            zoom_max=_ZOOM_MAX,
            zoom_step=_ZOOM_STEP,
        )
        self._cached_render_context: RenderContext | None = None
        self._hover_hit: HoverHit | None = None
        self._hover_highlight_overlay: tuple[float, float, float, float] | None = None
        self._pinned_target_id: str | None = None
        self._did_pan_during_drag = False
        self._stable_viewport: NormalizedViewport | None = None
        self._quick = QuickSurface(self)
        self._quick.setGeometry(self.rect())
        self._quick.hide()
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setSingleShot(True)
        self._prepare_timer.timeout.connect(self._prepare_quick_frame)
        self._hover_card = EntityHoverCard(self)

        GlobalSettings().overlay_preference_changed.connect(self._on_overlay_preference_changed)
        self.destroyed.connect(
            lambda _=None, instance_id=self._metrics_instance_id: get_render_metrics_store().remove(instance_id)
        )

    @Slot(VideoFrameWithOverlays)
    def display_frame(self, video_frame: VideoFrameWithOverlays) -> None:
        get_render_metrics_store().submit(self._metrics_instance_id, self._frame_identity(video_frame))
        self._video_frame = video_frame
        if self._pinned_target_id is not None:
            self._refresh_pinned_selection()
        else:
            self._refresh_hover_from_cursor()
        self._request_repaint()

    def clear(self) -> None:
        self._clear_selection()
        self._video_frame = None
        get_render_metrics_store().reset(self._metrics_instance_id)
        self._stable_viewport = None
        self._prepare_timer.stop()
        self._quick.clear()
        self._quick.hide()
        self._request_repaint()

    def refresh_last_frame(self) -> None:
        if self._video_frame is not None:
            if self._pinned_target_id is not None:
                self._refresh_pinned_selection()
            else:
                self._refresh_hover_from_cursor()
            self._request_repaint()

    def _request_repaint(self) -> None:
        if self._video_frame is None:
            self.update()
            return
        if not self._prepare_timer.isActive():
            self._prepare_timer.start(0)

    def _prepare_quick_frame(self) -> None:
        self._prepare_timer.stop()
        surface, frame = self._quick, self._video_frame
        if frame is None or self.width() <= 0 or self.height() <= 0:
            return
        capture = is_metrics_enabled()
        start_time = perf_counter() if capture else 0.0
        target = self._aspect_respected_rect(frame.frame.image.size())
        if target.width() < 1 or target.height() < 1:
            return
        context = self._create_render_context(target)
        image_start = perf_counter() if capture else 0.0
        surface.set_frame_image(frame.frame.image, target)
        compose_start = perf_counter() if capture else 0.0
        drawing, opacity = frame.prepare_overlays(context, surface.drawing_buffer(target, context.scale_factor))
        draw_start = perf_counter() if capture else 0.0
        surface.set_overlays(drawing, opacity, target, context.scale_factor, self._hover_highlight_overlay)
        draw_end = perf_counter() if capture else 0.0
        if surface.isHidden():
            surface.show()
            surface.lower()
        if capture:
            self._record_render(
                start_time,
                image_start,
                compose_start,
                draw_start,
                draw_end,
                context,
                primitive_count=sum(count for _, count in drawing.counts) if drawing is not None else 0,
                submitted_count=len(drawing) if drawing is not None else 0,
                opacity=opacity,
                backend=surface.backend_label,
            )

    def _record_render(
        self,
        start_time: float,
        image_start: float,
        compose_start: float,
        draw_start: float,
        draw_end: float,
        context: RenderContext,
        *,
        primitive_count: int,
        submitted_count: int,
        opacity: float,
        backend: str,
    ) -> None:
        assert self._video_frame is not None
        completed_at = perf_counter()
        overlay = self._video_frame.overlays
        provider = overlay.metrics_provider if overlay is not None else None
        get_render_metrics_store().record(
            self._metrics_instance_id,
            PaintSample(
                completed_at=completed_at,
                frame=self._frame_identity(self._video_frame),
                overlay=FrameIdentity(
                    overlay.overlay_id,
                    overlay.timestamp_monotime_us
                    if overlay.timestamp_monotime_us is not None
                    else overlay.timestamp * 1_000_000,
                )
                if overlay
                else None,
                overlay_reused=bool(overlay and overlay.metadata and overlay.metadata.get("overlay_reused", False)),
                paint_ms=(completed_at - start_time) * 1000,
                image_ms=(compose_start - image_start) * 1000,
                compose_ms=(draw_start - compose_start) * 1000,
                draw_ms=(draw_end - draw_start) * 1000,
                primitive_count=primitive_count,
                drawn_primitive_count=submitted_count if opacity > 0.0 else 0,
                generation=provider.latest_drawing_preparation_metrics() if provider else None,
                backend=backend,
                width=context.width,
                height=context.height,
            ),
            paint_started_at=start_time,
        )

    def set_diagnostics_label(self, label: str) -> None:
        """Associate rendering samples with the content/lane displayed by this viewer."""
        get_render_metrics_store().register(self._metrics_instance_id, label)

    def set_diagnostics_sources(self, source_ids: tuple[str, ...]) -> None:
        """Associate this viewer with the source observations used to produce its frames."""
        get_render_metrics_store().set_sources(self._metrics_instance_id, source_ids)

    @staticmethod
    def _frame_identity(frame: VideoFrameWithOverlays) -> FrameIdentity:
        video = frame.frame
        timestamp = video.timestamp_monotime_us
        return FrameIdentity(video.frame_id, timestamp if timestamp is not None else video.timestamp * 1_000_000)

    def cleanup(self) -> None:
        """Release the submitted frame, native resources, and rendering diagnostics."""
        self.clear()
        self._prepare_timer.stop()
        self._quick.cleanup()
        get_render_metrics_store().remove(self._metrics_instance_id)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._preserve_viewport_on_resize(event)
        super().resizeEvent(event)
        self._quick.setGeometry(self.rect())
        self._request_repaint()

    def _aspect_respected_rect(self, img_size: QtCore.QSize) -> QtCore.QRectF:
        base = self._base_rect(img_size)
        return self._viewport.compute_target_rect(base)

    def frame_display_rect(self) -> QtCore.QRectF | None:
        """Return the unzoomed frame rectangle fitted within the renderer."""
        if self._video_frame is None:
            return None
        return self._base_rect(self._video_frame.frame.image.size())

    def _base_rect(self, img_size: QtCore.QSize) -> QtCore.QRectF:
        return self._base_rect_for_container(img_size, self.rect())

    def _base_rect_for_container(
        self, img_size: QtCore.QSize, widget_rect: QtCore.QRect | QtCore.QRectF
    ) -> QtCore.QRectF:
        content_size = (img_size.width(), img_size.height())
        container_size = (round(widget_rect.width()), round(widget_rect.height()))
        transform = calculate_aspect_preserving_fit(content_size, container_size)
        fitted_w, fitted_h = transform.fitted_size
        offset_x, offset_y = transform.center_offset
        return QtCore.QRectF(widget_rect.left() + offset_x, widget_rect.top() + offset_y, fitted_w, fitted_h)

    def _preserve_viewport_on_resize(self, event: QResizeEvent) -> None:
        if self._video_frame is None or self._viewport.zoom_level <= self._viewport.zoom_min:
            return

        old_size = event.oldSize()
        new_size = event.size()
        if new_size.isEmpty():
            return

        image_size = self._video_frame.frame.image.size()
        new_rect = QtCore.QRectF(0.0, 0.0, float(new_size.width()), float(new_size.height()))
        new_base = self._base_rect_for_container(image_size, new_rect)
        if new_base.isEmpty():
            return
        if self._stable_viewport is None:
            if not old_size.isValid() or old_size.isEmpty():
                return
            old_rect = QtCore.QRectF(0.0, 0.0, float(old_size.width()), float(old_size.height()))
            old_base = self._base_rect_for_container(image_size, old_rect)
            if old_base.isEmpty():
                return
            self._stable_viewport = self._viewport.to_normalized(old_base)
        self._viewport.apply_normalized(self._stable_viewport, new_base, new_rect)

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

        if self._pinned_target_id is not None:
            self._refresh_pinned_selection()

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
            if self._pinned_target_id is not None:
                self._refresh_pinned_selection()
            else:
                self._clear_hover()
            self._request_repaint()
            self._emit_viewport_changed()
            event.accept()
        elif self._pinned_target_id is not None:
            super().mouseMoveEvent(event)
        else:
            self._update_hover(event)
            super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802
        self._clear_hover()
        super().leaveEvent(event)

    def _handle_click_selection(self, pos: QtCore.QPointF) -> bool:
        """Handle click-to-pin selection. Returns True when click was in video area."""
        target = self._hover_target_rect()
        if target is None or not target.contains(pos):
            return False

        nx = (pos.x() - target.x()) / target.width()
        ny = (pos.y() - target.y()) / target.height()

        hit = self._hit_test(nx, ny)
        if hit is None:
            self._clear_selection()
            return True

        self._pinned_target_id = hit.target_id
        if self._update_hover_hit(hit):
            self._request_repaint()
        self._show_hover_card_for_hit(hit, target)
        return True

    def _update_hover(self, event: QMouseEvent) -> None:
        """Hit-test entities at the cursor position and update the hover card."""
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

    def _refresh_pinned_selection(self) -> None:
        """Refresh pinned selection against the latest frame/provider state."""
        if self._pinned_target_id is None:
            return

        target = self._hover_target_rect()
        interaction_provider = self._interaction_provider()
        if target is None or interaction_provider is None:
            self._clear_selection()
            return

        hit = interaction_provider.get_hit_by_id(self._pinned_target_id)
        if hit is None:
            self._clear_selection()
            return

        if self._update_hover_hit(hit):
            self._request_repaint()
        self._show_hover_card_for_hit(hit, target)

    def _update_hover_at_position(self, pos: QtCore.QPointF) -> None:
        """Hit-test entities at a widget-space cursor position and update hover UI."""
        if self._pinned_target_id is not None:
            return

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
        self._show_hover_card_for_hit(hit, target)

    def _hover_target_rect(self) -> QtCore.QRectF | None:
        if self._video_frame is None or self._video_frame.overlays is None:
            return None
        return self._aspect_respected_rect(self._video_frame.frame.image.size())

    def _show_hover_card_for_hit(self, hit: HoverHit | None, target: QtCore.QRectF) -> None:
        if hit is None:
            self._hover_card.hide_card()
            return

        anchor = self._hover_card_anchor_point(hit, target)
        avoid_rect = self._hover_card_avoid_rect(hit, target)
        self._hover_card.show_for(
            hit.target_id,
            hit.card_html,
            int(anchor.x()),
            int(anchor.y()),
            avoid_rect=avoid_rect,
        )

    def _hover_card_anchor_point(self, hit: HoverHit, target: QtCore.QRectF) -> QtCore.QPointF:
        """Return widget-space anchor point near the target bounds."""
        x, y, w, _h = hit.bounds
        anchor_x = target.x() + (x + w) * target.width()
        anchor_y = target.y() + y * target.height()
        return QtCore.QPointF(anchor_x, anchor_y)

    def _hover_card_avoid_rect(self, hit: HoverHit, target: QtCore.QRectF) -> tuple[int, int, int, int]:
        """Return widget-space bounds that the hover card should avoid covering when possible."""
        x, y, w, h = hit.bounds
        rect_x = round(target.x() + x * target.width())
        rect_y = round(target.y() + y * target.height())
        rect_w = max(1, round(w * target.width()))
        rect_h = max(1, round(h * target.height()))
        return (rect_x, rect_y, rect_w, rect_h)

    def _update_hover_hit(self, hit: HoverHit | None) -> bool:
        """Retain the hit and rebuild the highlight only when its geometry changes."""
        previous = self._hover_hit
        self._hover_hit = hit
        bounds = hit.bounds if hit else None
        if bounds == (previous.bounds if previous else None):
            return False
        self._hover_highlight_overlay = bounds
        return True

    def _clear_hover(self, *, force: bool = False) -> None:
        if self._pinned_target_id is not None and not force:
            return

        if self._hover_hit is not None or self._hover_highlight_overlay is not None:
            self._hover_hit = None
            self._hover_highlight_overlay = None
            self._request_repaint()
        self._hover_card.hide_card()

    def _clear_selection(self) -> None:
        self._pinned_target_id = None
        self._clear_hover(force=True)

    def _on_overlay_preference_changed(self, preference: OverlayPreference, enabled: bool) -> None:
        """Drop the hover card and pinned selection when object interaction is turned off."""
        if preference is OverlayPreference.HOVER and not enabled:
            self._clear_selection()
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
