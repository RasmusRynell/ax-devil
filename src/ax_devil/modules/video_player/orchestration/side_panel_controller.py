"""Side panel orchestration for video player widgets."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from ax_devil.modules.settings.logging_config import get_logger

from ..ui.draggable import DraggableHandle, DraggablePanel
from ..ui.fading import FadingWidget
from ..ui.overlay_layout import OverlayPosition
from ..ui.viewport import FrameViewport
from .interaction_types import HoverSink

logger = get_logger(__name__)


class SidePanelController:
    """Manages draggable side panel lifecycle and wiring."""

    def __init__(
        self,
        *,
        player_widget: QWidget,
        video_widget: FrameViewport,
        hover_sink: HoverSink,
        connect_controls_visibility: Callable[[FadingWidget], None],
        panel_toggled: Callable[[bool], None],
    ) -> None:
        self._logger = logger
        self._player_widget = player_widget
        self._video_widget = video_widget
        self._hover_sink = hover_sink
        self._connect_controls_visibility = connect_controls_visibility
        self._panel_toggled = panel_toggled

        self._side_panel: DraggablePanel | None = None
        self._drag_handle: DraggableHandle | None = None

    def enable(self, initial_widget: QWidget | None = None) -> None:
        if self._side_panel is not None:
            self._logger.warning("Side panel already enabled")
            return

        self._side_panel = DraggablePanel(parent=self._player_widget)
        self._side_panel.set_hover_sink(self._hover_sink)
        if initial_widget is not None:
            self._side_panel.set_content_widget(initial_widget)

        layout = self._player_widget.layout()
        if layout is not None:
            layout.addWidget(self._side_panel)

        self._drag_handle = DraggableHandle(parent=self._video_widget)
        self._drag_handle.setTarget(self._side_panel)
        self._drag_handle.set_hover_sink(self._hover_sink)

        self._video_widget.add_overlay(self._drag_handle, position=OverlayPosition.RIGHT_CENTER)
        self._side_panel.panelToggled.connect(self._video_widget.position_overlays)
        self._side_panel.panelToggled.connect(self._panel_toggled)
        self._connect_controls_visibility(self._drag_handle)

        self._video_widget.position_overlays()
        self._logger.debug("Side panel enabled")

    def set_content_widget(self, widget: QWidget | None) -> None:
        if self._side_panel is None:
            self._logger.warning("Side panel not enabled. Call enable_side_panel() first.")
            return

        self._side_panel.set_content_widget(widget)
        widget_name = widget.__class__.__name__ if widget else "None"
        self._logger.debug(f"Side panel widget set: {widget_name}")

    def fit_to_pane(self, pane_width: int) -> None:
        """Size the panel for a pane *pane_width* wide; a pane too narrow for panel and video closes the panel."""
        if self._side_panel is not None:
            self._side_panel.fit_to_pane(pane_width)

    def yielding_width(self) -> int:
        """Return the width an open panel gives up to a narrowing pane by closing, or 0."""
        return self._side_panel.yielding_width() if self._side_panel is not None else 0

    def is_open(self) -> bool:
        """Return whether the side panel is expanded or expanding."""
        return self._side_panel is not None and not self._side_panel.is_collapsed

    def set_open(self, is_open: bool) -> None:
        """Expand or collapse the side panel; does nothing before ``enable``."""
        if self._side_panel is None:
            return
        if is_open:
            self._side_panel.expand()
        else:
            self._side_panel.collapse()

    def cleanup(self) -> None:
        """Detach and delete the side-panel mount widgets."""
        if self._side_panel is not None:
            try:
                self._side_panel.panelToggled.disconnect(self._video_widget.position_overlays)
                self._side_panel.panelToggled.disconnect(self._panel_toggled)
            except Exception:
                self._logger.debug("panelToggled disconnect failed or already disconnected")

        if self._drag_handle is not None:
            self._drag_handle.cleanup()
            self._drag_handle.target_object = None
            self._video_widget.remove_overlay(self._drag_handle, delete_widget=True)
            self._drag_handle = None

        if self._side_panel is not None:
            self._side_panel.cleanup()
            layout = self._player_widget.layout()
            if layout is not None:
                layout.removeWidget(self._side_panel)
            self._side_panel.setParent(None)
            self._side_panel.deleteLater()
            self._side_panel = None
