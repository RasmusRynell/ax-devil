"""Viewer widget for live content in the workspace-driven UI."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.video_player.ui.controls import video_control_icon
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.overlay_layout import OverlayPosition
from ax_devil.modules.video_viewer.live_connection import LiveConnectionStatus, LiveFeed
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.stream_media_controller import StreamMediaController
from ax_devil.modules.workspace import LiveVideoContent
from ax_devil.modules.workspace.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.viewer_host import WorkspaceWidget

logger = get_logger(__name__)


class LiveStatusPanel(QWidget):
    """Caller-owned status panel for the live Video Viewer workflow."""

    pauseToggled = Signal()
    """Emitted when the pause/play button is clicked."""
    retryRequested = Signal()
    """Emitted when the user asks to reconnect a failed or stalled stream."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setObjectName("LiveStatusPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            #LiveStatusPanel {
                background: rgba(0, 0, 0, 140);
            }
            #LiveStatusPanel QLabel {
                background: transparent;
                color: white;
            }
        """)
        TextRole.STRONG.apply(self)

        rows = QVBoxLayout(self)
        rows.setContentsMargins(Space.L, Space.M, Space.L, Space.M)
        rows.setSpacing(Space.S)
        layout = QHBoxLayout()
        layout.setSpacing(Space.M)
        rows.addLayout(layout)

        self._pause_button = QPushButton(self)
        self._pause_button.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: white;
                padding: {Space.S}px {Space.M}px;
                border: none;
            }}
        """)
        self._pause_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pause_button.setToolTip("Pause")
        self._pause_button.clicked.connect(self.pauseToggled.emit)
        self._update_pause_button_icon(paused=False)

        self._state_label = QLabel(self)

        self._paused_label = QLabel("PAUSED", self)
        self._paused_label.setStyleSheet("color: #ff4444;")
        self._paused_label.setVisible(False)

        self._timestamp_label = QLabel("00:00:00", self)
        self._frame_label = QLabel("Frame: 0", self)

        layout.addWidget(self._pause_button)
        layout.addWidget(self._state_label)
        layout.addWidget(self._paused_label)
        layout.addWidget(self._timestamp_label)
        layout.addStretch(1)
        layout.addWidget(self._frame_label)

        self._problem_row = QWidget(self)
        problem_layout = QHBoxLayout(self._problem_row)
        problem_layout.setContentsMargins(0, 0, 0, 0)
        problem_layout.setSpacing(Space.M)
        self._problem_label = QLabel(self._problem_row)
        self._problem_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        TextRole.BODY.apply(self._problem_label)
        self._problem_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._retry_button = QPushButton("Retry", self._problem_row)
        self._retry_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._retry_button.setToolTip("Reconnect to the device")
        self._retry_button.clicked.connect(self.retryRequested.emit)
        problem_layout.addWidget(self._problem_label, 1)
        problem_layout.addWidget(self._retry_button)
        rows.addWidget(self._problem_row)

    def set_paused(self, paused: bool) -> None:
        """Update button icon and paused indicator visibility."""
        self._paused_label.setVisible(paused)
        self._update_pause_button_icon(paused)

    def _update_pause_button_icon(self, paused: bool) -> None:
        """Set the button icon based on pause state."""
        self._pause_button.setIcon(video_control_icon(Icon.PLAY if paused else Icon.PAUSE))
        self._pause_button.setToolTip("Resume" if paused else "Pause")

    def show_connection_status(self, status: LiveConnectionStatus) -> None:
        """Show the stream state, any feed problems and whether a retry is needed."""
        headline = status.headline
        self._state_label.setText(headline.headline)
        self._state_label.setStyleSheet(f"color: {headline.state.color};")
        self._state_label.setToolTip(headline.state.description)
        problems = "\n".join(problem.summary for problem in status.problems)
        self._problem_label.setText(problems)
        self._problem_label.setToolTip(problems)
        self._problem_row.setVisible(bool(status.problems))
        self._retry_button.setVisible(status.needs_retry)
        self.adjustSize()

    def display_frame_status(self, frame: VideoFrameWithOverlays) -> None:
        """Update status labels from the displayed live frame."""
        timestamp = frame.frame.timestamp
        if timestamp is not None:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            self._timestamp_label.setText(f"{dt:%H:%M:%S}.{dt.microsecond:06d}")
        else:
            logger.warning("Frame has no timestamp")

        frame_id = frame.frame.frame_id
        if frame_id is not None:
            self._frame_label.setText(f"Frame: {frame_id}")
        else:
            logger.warning("Frame has no frame_id")


class LiveVideoViewerWidget(WorkspaceWidget):
    """Viewer for one ``LiveVideoContent`` item."""

    def __init__(
        self,
        content: LiveVideoContent,
        render_catalog_manager: SceneRenderCatalogManager,
        parent: QWidget | None = None,
    ) -> None:
        self._content = content
        self._render_catalog_manager = render_catalog_manager
        self._render_catalog_selection = self._render_catalog_manager.create_selection()
        self._controller: StreamMediaController | None = None
        self._tools_panel: MediaToolsPanel | None = None
        self._frame_display: FrameDisplay | None = None
        self._status_panel: LiveStatusPanel | None = None
        super().__init__(parent)

    def _setup_widget_ui(self) -> None:
        """Set up the live viewer."""
        layout = self.get_content_layout()

        display = FrameDisplay()
        display.viewport.set_diagnostics_label(self._content.display_name)
        status_panel = LiveStatusPanel(display)
        status_panel.pauseToggled.connect(self.toggle_playback)
        status_panel.retryRequested.connect(self.retry_connection)
        display.mount_overlay(status_panel, position=OverlayPosition.BOTTOM_FULL_WIDTH)
        display.enable_side_panel()
        layout.addWidget(display)
        self._frame_display = display
        self._status_panel = status_panel
        self._show_connection_status(LiveConnectionStatus.connecting([LiveFeed.VIDEO]))

        try:
            controller = StreamMediaController(
                display,
                self._content,
                render_catalog_selection=self._render_catalog_selection,
                frame_displayed=status_panel.display_frame_status,
                connection_changed=self._show_connection_status,
            )
            self._controller = controller
            self._attach_tools_panel(display, controller)
            controller.start_playback()
        except Exception as e:
            self.cleanup()
            logger.debug(traceback.format_exc())
            logger.error(f"Failed to start live stream: {e}")
            raise RuntimeError(f"Failed to start live stream: {e}") from e

    def _show_connection_status(self, status: LiveConnectionStatus) -> None:
        """Show connection state in the status panel and in the pane before the first frame."""
        if self._status_panel is not None:
            self._status_panel.show_connection_status(status)
        if self._frame_display is not None:
            self._frame_display.viewport.set_background_text(status.placeholder_text)
            self._frame_display.viewport.position_overlays()

    def retry_connection(self) -> None:
        """Reopen the live stream after a failure; playback resumes."""
        if self._controller is None:
            return
        self._controller.retry()
        if self._status_panel is not None:
            self._status_panel.set_paused(False)

    def _attach_tools_panel(
        self,
        display: FrameDisplay,
        controller: StreamMediaController,
    ) -> None:
        """Create and wire a MediaToolsPanel to the display and controller."""
        tools_panel = MediaToolsPanel(
            self._render_catalog_selection,
            filter_config=controller.get_filter_config(),
            overlay_settings=OverlayPersistenceSettings.default_enabled(),
        )
        display.set_side_panel_widget(tools_panel)
        controller.attach_filter_widget(tools_panel.filter_widget)
        controller.attach_scene_inspector(tools_panel)
        tools_panel.catalogViewerRequested.connect(self._open_catalog_viewer)
        tools_panel.overlayPersistenceChanged.connect(controller.set_overlay_persistence)
        self._tools_panel = tools_panel

    def _open_catalog_viewer(self) -> None:
        from ax_devil.modules.catalog_viewer import show_catalog_viewer

        show_catalog_viewer(
            self._render_catalog_manager,
            parent=self,
            catalog_path=self._render_catalog_selection.active_catalog_path(),
        )

    def get_display_name(self) -> str:
        """Return the display name from the content item."""
        return self._content.display_name

    def pause_playback(self) -> None:
        """Pause the live stream. No-op if already paused."""
        if self._controller is None or self._controller.is_paused:
            return
        self._controller.pause_playback()
        if self._status_panel is not None:
            self._status_panel.set_paused(True)

    def toggle_playback(self) -> None:
        """Toggle between playing and paused states for the live stream."""
        if self._controller is None:
            return
        if self._controller.is_paused:
            self._controller.resume_playback()
        else:
            self._controller.pause_playback()
        if self._status_panel is not None:
            self._status_panel.set_paused(self._controller.is_paused)

    def toggle_media_tools(self) -> None:
        """Show or hide the media tools panel."""
        if self._frame_display is not None:
            self._frame_display.set_side_panel_open(not self._frame_display.is_side_panel_open())

    def zoom(self, step: ZoomStep) -> None:
        """Apply one keyboard zoom step to the live display."""
        if self._frame_display is not None:
            self._frame_display.zoom(step)

    def current_on_screen_item(self) -> OnScreenWorkspaceItem:
        """Return the workspace item shown by this viewer."""
        return self._content.on_screen_item()

    def cleanup(self) -> None:
        """Tear down controller, display, and tools panel."""
        if self._controller:
            try:
                self._controller.cleanup()
            except Exception:
                logger.exception("Live stream controller failed to clean up")
            self._controller = None
        if self._tools_panel:
            try:
                if self._frame_display:
                    self._frame_display.set_side_panel_widget(None)
                self._tools_panel.cleanup()
                self._tools_panel.deleteLater()
            except Exception:
                logger.exception("Live media tools failed to clean up")
            self._tools_panel = None
        if self._frame_display:
            try:
                self._frame_display.cleanup()
                self._frame_display.setParent(None)
                self._frame_display.deleteLater()
            except Exception:
                logger.exception("Live frame display failed to clean up")
            self._frame_display = None
        self._status_panel = None
