"""Lower-level frame viewing area built on top of the frame renderer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QPainter, QPaintEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QWidget

from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings

from ..constants import INFO_OVERLAY_BACKGROUND_ALPHA
from ..engine.data_types import VideoFrameWithOverlays
from ..engine.renderer import VideoFrameRenderer
from ..engine.viewport_state import NormalizedViewport
from .hud_painter import HudPainter
from .overlay_layout import OverlayPosition, calculate_overlay_placement

_TIMING_DEBUG_LABELS = {
    "video_timestamp_source": "Timestamp source",
    "video_pts": "PTS",
    "video_first_pts": "First PTS",
    "video_pts_delta": "PTS delta",
    "video_time_base": "Time base",
    "video_period_after_s": "Period after",
    "video_period_source": "Period source",
    "requested_timestamp_us": "Requested timestamp",
    "matched_timestamp_us": "Matched timestamp",
    "timestamp_match_type": "Match type",
    "timestamp_fallback_mode": "Fallback mode",
    "timestamp_tolerance_us": "Tolerance",
    "timestamp_effective_tolerance_us": "Effective tolerance",
    "overlay_alignment_basis": "Alignment basis",
    "timestamp_offset_us": "Lookup offset",
    "requested_sequence_id": "Requested sequence",
    "matched_sequence_id": "Matched sequence",
}


@dataclass(frozen=True, slots=True)
class _ManagedOverlay:
    """Overlay registration entry with explicit layout and visibility intent."""

    widget: QWidget
    position: OverlayPosition
    hide_while_inspecting: bool
    preference: OverlayPreference | None

    @property
    def viewport_owns_visibility(self) -> bool:
        """Return whether the viewport shows and hides this widget instead of its owner."""
        return self.hide_while_inspecting or self.preference is not None


class _InfoHud(QWidget):
    """Transparent widget layer above the video surface and below controls."""

    def __init__(self, viewport: "FrameViewport") -> None:
        super().__init__(viewport)
        self._viewport = viewport
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        HudPainter.draw_info_overlay(
            painter,
            self,
            self._viewport._stats,
            background_alpha=INFO_OVERLAY_BACKGROUND_ALPHA,
        )
        painter.end()


class FrameViewport(VideoFrameRenderer):
    """Lower-level viewing area for frame and overlay presentation.

    Owns frame presentation, viewport interaction, hover reporting, and
    visual overlay positioning. FrameViewport is not the public workflow
    composition surface; use ``FrameDisplay`` for that."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._background_text: str = "Frame display ready\n\nLoad video content to begin"
        self._overlay_visible: bool = False
        self._stats: dict[str, Any] = {}
        self._overlays: list[_ManagedOverlay] = []
        self._info_hud = _InfoHud(self)
        self._info_hud.setGeometry(self.rect())
        self.viewportChanged.connect(self._on_viewport_changed)
        GlobalSettings().overlay_preference_changed.connect(self._on_overlay_visibility_preference_changed)

    @Slot(VideoFrameWithOverlays)
    def display_frame(self, video_frame: VideoFrameWithOverlays) -> None:
        super().display_frame(video_frame)
        self._stats = self._build_info_overlay_stats(video_frame)
        self._update_info_hud()
        if self._overlays and self._can_position_overlays():
            self.position_overlays()

    def set_viewport(self, viewport: NormalizedViewport) -> None:
        super().set_viewport(viewport)
        self._update_overlay_visibility()

    def set_background_text(self, text: str) -> None:
        """Set the text shown while no frame is displayed."""
        self._background_text = text
        self.update()

    def clear(self) -> None:
        super().clear()
        self._stats = {}
        self._info_hud.hide()

    def cleanup(self) -> None:
        """Clear frame, overlay widgets, and hover state before display teardown."""
        for managed in list(self._overlays):
            self.remove_overlay(managed.widget, delete_widget=True)
        super().cleanup()

    def toggle_info_overlay(self) -> None:
        self._overlay_visible = not self._overlay_visible
        self._update_overlay_visibility()
        self._update_info_hud()
        self._logger.debug(f"Info overlay toggled: {self._overlay_visible}")
        self.update()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._overlays and self._can_position_overlays():
            self.position_overlays()
            self._logger.debug("Overlay positioning completed on show")

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        if self._video_frame is None:
            painter = QPainter(self)
            self._draw_background_content(painter)
            painter.end()

    def _draw_background_content(self, painter: QPainter) -> None:
        HudPainter.draw_background_content(painter, self, self._background_text)

    def _update_info_hud(self) -> None:
        self._info_hud.setVisible(self._overlay_visible and bool(self._stats))
        self._info_hud.update()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Keep the diagnostic HUD above the full video surface."""
        super().resizeEvent(event)
        self._info_hud.setGeometry(self.rect())
        self.position_overlays()

    def _build_info_overlay_stats(self, video_frame: VideoFrameWithOverlays) -> dict[str, Any]:
        frame = video_frame.frame
        overlay = video_frame.overlays
        stats: dict[str, Any] = {
            "Timing": None,
            "Unit": "microseconds",
            "Status": self._causal_status_text(frame.timestamp_monotime_us, None),
            "Frame - overlay": "None",
            "Frame": None,
            "Frame id": self._format_optional_id(frame.frame_id),
            "Frame timestamp": self._format_optional_timestamp_us(frame.timestamp_monotime_us),
        }
        self._add_timing_metadata(stats, frame.metadata)
        if overlay is None:
            stats.update(
                {
                    "Overlay": None,
                    "Overlay id": "None",
                    "Overlay timestamp": "None",
                }
            )
            return stats

        stats.update(
            {
                "Status": self._causal_status_text(frame.timestamp_monotime_us, overlay.timestamp_monotime_us),
                "Frame - overlay": self._format_timestamp_delta_us(
                    frame.timestamp_monotime_us,
                    overlay.timestamp_monotime_us,
                ),
                "Overlay": None,
                "Overlay id": self._format_optional_id(overlay.overlay_id),
                "Overlay timestamp": self._format_optional_timestamp_us(overlay.timestamp_monotime_us),
            }
        )
        self._add_timing_metadata(stats, overlay.metadata)
        return stats

    @classmethod
    def _causal_status_text(cls, frame_timestamp_us: float | None, overlay_timestamp_us: float | None) -> str:
        if overlay_timestamp_us is None:
            return "NO OVERLAY"
        if frame_timestamp_us is None:
            return "UNKNOWN frame timestamp missing"
        delta_us = frame_timestamp_us - overlay_timestamp_us
        if delta_us < 0:
            return f"VIOLATION overlay is {cls._format_optional_timestamp_us(abs(delta_us))} after frame"
        if delta_us == 0:
            return "OK exact timestamp match"
        return "OK overlay is before frame"

    @classmethod
    def _add_timing_metadata(cls, stats: dict[str, Any], metadata: dict[str, Any] | None) -> None:
        if not metadata:
            return
        for key, value in metadata.items():
            label = _TIMING_DEBUG_LABELS.get(key)
            if label is None:
                continue
            stats[label] = cls._format_timing_metadata_value(key, value)

    @classmethod
    def _format_timing_metadata_value(cls, key: str, value: Any) -> Any:
        if key == "video_period_after_s" and isinstance(value, int | float):
            return cls._format_optional_timestamp_us(float(value) * 1_000_000.0)
        return value

    @staticmethod
    def _format_optional_id(value: int | None) -> str:
        if value is None:
            return "None"
        return str(value)

    @staticmethod
    def _format_optional_timestamp_us(value: float | int | None) -> str:
        if value is None:
            return "None"
        if isinstance(value, int):
            return str(value)
        if value.is_integer():
            return str(int(value))
        return str(value)

    @classmethod
    def _format_timestamp_delta_us(cls, frame_timestamp_us: float | None, overlay_timestamp_us: float | None) -> str:
        if frame_timestamp_us is None or overlay_timestamp_us is None:
            return "None"
        return cls._format_optional_timestamp_us(frame_timestamp_us - overlay_timestamp_us)

    def add_overlay(
        self,
        overlay: QWidget,
        *,
        position: OverlayPosition = OverlayPosition.BOTTOM_CENTER,
        hide_while_inspecting: bool = False,
        preference: OverlayPreference | None = None,
    ) -> None:
        overlay.setParent(self)
        self._overlays.append(
            _ManagedOverlay(
                widget=overlay,
                position=position,
                hide_while_inspecting=hide_while_inspecting,
                preference=preference,
            )
        )
        self._update_overlay_visibility()
        self._logger.debug(f"Added overlay: {overlay.__class__.__name__}")

        if self._can_position_overlays():
            self.position_overlays()

    def remove_overlay(self, overlay: QWidget, *, delete_widget: bool = False) -> None:
        """Unmount one overlay widget from the viewport."""
        self._overlays = [managed for managed in self._overlays if managed.widget is not overlay]
        overlay.setParent(None)
        if delete_widget:
            overlay.deleteLater()

    def _on_viewport_changed(self, _viewport: NormalizedViewport) -> None:
        self._update_overlay_visibility()

    def _on_overlay_visibility_preference_changed(self, _preference: OverlayPreference, _enabled: bool) -> None:
        self._update_overlay_visibility()

    def _update_overlay_visibility(self) -> None:
        """Hide opted-in overlays while the frame is inspected (zoomed in or showing info) or turned off."""
        inspecting = self._overlay_visible or self._viewport.zoom_level > self._viewport.zoom_min
        settings = GlobalSettings()
        for managed in self._overlays:
            if managed.viewport_owns_visibility:
                managed.widget.setVisible(
                    not (managed.hide_while_inspecting and inspecting)
                    and (managed.preference is None or settings.is_overlay_enabled(managed.preference))
                )

    def _can_position_overlays(self) -> bool:
        return self.isVisible() and self.width() > 0 and self.height() > 0

    def position_overlays(self) -> None:
        if not self._overlays:
            return

        display_width = self.width()
        display_height = self.height()
        displayed_frame = self.frame_display_rect()
        frame_rect = None
        if displayed_frame is not None:
            frame_rect = (
                round(displayed_frame.x()),
                round(displayed_frame.y()),
                round(displayed_frame.width()),
                round(displayed_frame.height()),
            )

        for managed in self._overlays:
            instruction = calculate_overlay_placement(
                managed.position,
                overlay=managed.widget,
                display_width=display_width,
                display_height=display_height,
                frame_rect=frame_rect,
            )
            if instruction.fixed_width is not None:
                managed.widget.setFixedWidth(instruction.fixed_width)

            placement = instruction.placement
            managed.widget.move(placement.x, placement.y)
            self._logger.debug(
                f"Positioned {managed.widget.__class__.__name__} overlay at ({placement.x}, {placement.y}) "
                f"using {managed.position.value} strategy"
            )
