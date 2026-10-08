"""Public reusable display shell for frames, overlays, and mounted widgets."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QEnterEvent, QMouseEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference

from ..constants import MOUSE_IDLE_HIDE_DELAY
from ..engine.data_types import VideoFrameWithOverlays
from ..engine.viewport_state import NormalizedViewport, ZoomStep
from ..orchestration.control_visibility import ControlVisibilityController
from ..orchestration.side_panel_controller import SidePanelController
from .fading import FadingWidget
from .overlay_layout import OverlayPosition
from .viewport import FrameViewport

logger = get_logger(__name__)


class FrameDisplay(QWidget):
    """Reusable display shell with generic mount points for caller-owned UI."""

    aboutToCleanup = Signal()
    viewportChanged = Signal(object)  # NormalizedViewport
    controlsRequested = Signal()
    controlsHideRequested = Signal()
    sidePanelToggled = Signal(bool)  # True when the side panel opens, False when it closes

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setMouseTracking(True)

        self._viewport = FrameViewport(self)
        self._viewport.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._initial_focus_claimed = False
        self._controls: list[QWidget] = []
        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)
        self._main_layout.addWidget(self._viewport, 1)
        self._viewport.viewportChanged.connect(self.viewportChanged)

        self._visibility = ControlVisibilityController(
            timer_parent=self,
            idle_hide_delay_ms=MOUSE_IDLE_HIDE_DELAY,
            request_show=self.controlsRequested.emit,
            request_hide=self.controlsHideRequested.emit,
        )
        self._side_panel_controller = SidePanelController(
            player_widget=self,
            video_widget=self._viewport,
            hover_sink=self,
            connect_controls_visibility=self._connect_controls_visibility,
            panel_toggled=self.sidePanelToggled.emit,
        )

    @property
    def viewport(self) -> FrameViewport:
        """Return the lower-level viewport owned by this display."""
        return self._viewport

    def display_frame(self, video_frame: VideoFrameWithOverlays) -> None:
        """Show a frame and any rendered overlays through the viewport."""
        self._viewport.display_frame(video_frame)

    def clear(self) -> None:
        """Clear the viewport and restore its empty display state."""
        self._viewport.clear()

    def refresh_overlays(self) -> None:
        """Request a repaint using the most recent frame and overlay state."""
        self._viewport.refresh_last_frame()

    def zoom(self, step: ZoomStep) -> None:
        """Apply one keyboard zoom step to the viewport."""
        self._viewport.zoom(step)

    def set_viewport(self, viewport: NormalizedViewport) -> None:
        """Apply viewport state without emitting ``viewportChanged``."""
        self._viewport.set_viewport(viewport)

    def mount_overlay(
        self,
        widget: QWidget,
        *,
        position: OverlayPosition,
        hide_while_inspecting: bool = False,
        preference: OverlayPreference | None = None,
    ) -> None:
        """Mount a caller-owned widget as a viewport overlay.

        With *hide_while_inspecting* the widget hides while the video is zoomed in or showing frame info; with
        *preference* it shows only while that overlay preference is on. Either hands its visibility to the viewport.
        """
        self._controls.append(widget)
        self._viewport.add_overlay(
            widget,
            position=position,
            hide_while_inspecting=hide_while_inspecting,
            preference=preference,
        )

        if isinstance(widget, FadingWidget):
            widget.set_hover_sink(self)
            self._connect_controls_visibility(widget)
            widget.fade_in()

    def enable_side_panel(self, initial_widget: QWidget | None = None) -> None:
        """Enable the generic side-panel mount point and optional initial content, fitted to the current width."""
        self._side_panel_controller.enable(initial_widget)
        self._side_panel_controller.fit_to_pane(self.width())

    def set_side_panel_widget(self, widget: QWidget | None) -> None:
        """Set caller-owned content in the side-panel mount point."""
        self._side_panel_controller.set_content_widget(widget)

    def is_side_panel_open(self) -> bool:
        """Return whether the side panel is open."""
        return self._side_panel_controller.is_open()

    def set_side_panel_open(self, is_open: bool) -> None:
        """Open or close the side panel, as its drag handle does; the panel reports through ``sidePanelToggled``."""
        self._side_panel_controller.set_open(is_open)

    def on_hover_enter(self, element_name: str) -> None:
        """Track when mouse enters a fade-controlled display element."""
        self._visibility.on_hover_enter(element_name)

    def on_hover_leave(self, element_name: str) -> None:
        """Track when mouse leaves a fade-controlled display element."""
        self._visibility.on_hover_leave(element_name)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Focus the viewport on first show so playback shortcuts work before any click."""
        super().showEvent(event)
        # Without this, macOS gives initial focus to the first text field (a side-panel search box).
        if not self._initial_focus_claimed:
            self._initial_focus_claimed = True
            self._viewport.setFocus()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Keep an open side panel at its share of the pane."""
        super().resizeEvent(event)
        self._side_panel_controller.fit_to_pane(event.size().width())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse movement for optional fading mount-point chrome."""
        self._visibility.on_mouse_move()
        super().mouseMoveEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        """Handle pointer entry for optional fading mount-point chrome."""
        self._visibility.on_mouse_enter()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        """Handle pointer leave for optional fading mount-point chrome."""
        self._visibility.on_mouse_leave()
        super().leaveEvent(event)

    def cleanup(self) -> None:
        """Clean up display-owned lifecycle controllers and mounted widgets."""
        self.aboutToCleanup.emit()
        self._visibility.cleanup()
        self._side_panel_controller.cleanup()
        for widget in self._controls:
            if isinstance(widget, FadingWidget):
                widget.cleanup()
            self._viewport.remove_overlay(widget, delete_widget=True)
        self._controls.clear()
        self._viewport.cleanup()

    def _connect_controls_visibility(self, widget: FadingWidget) -> None:
        self.controlsRequested.connect(widget.fade_in)
        self.controlsHideRequested.connect(widget.start_auto_hide_timer)
