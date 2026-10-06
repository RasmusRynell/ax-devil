"""Tests for the live-only ``LiveVideoViewerWidget``."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_player import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.quick.preparation import PreparedDrawing
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.live_connection import LiveConnectionStatus, LiveFeed
from ax_devil.modules.video_viewer.live_video_viewer import LiveVideoViewerWidget
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.workspace import LiveRTSPStreamSpec, LiveVideoContent


def _make_live_content(name: str = "Camera 1") -> LiveVideoContent:
    return LiveVideoContent(
        display_name=name,
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass"),
        overlays=(),
    )


def _make_display_frame() -> VideoFrameWithOverlays:
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(0)
    overlays = VideoOverlayData(
        drawing_generator=lambda _context, _settings: PreparedDrawing(),
        timestamp=10.0,
    )
    return VideoFrameWithOverlays(
        frame=VideoFrame(image=image, timestamp=10.25, frame_id=42),
        overlays=overlays,
    )


class _FakeStreamMediaController:
    """Controller test double that captures live viewer composition."""

    instances: list["_FakeStreamMediaController"] = []

    def __init__(
        self,
        frame_display: FrameDisplay,
        content: LiveVideoContent,
        *,
        render_catalog_selection: object,
        frame_displayed: Callable[[VideoFrameWithOverlays], None],
        connection_changed: Callable[[LiveConnectionStatus], None],
    ) -> None:
        self.frame_display = frame_display
        self.connection_changed = connection_changed
        self.connection_status = LiveConnectionStatus.connecting([LiveFeed.VIDEO, LiveFeed.OVERLAY])
        self.retries = 0
        self.content = content
        self.frame_displayed = frame_displayed
        self.started = False
        self.cleaned = False
        self.filter_widget: object | None = None
        self.scene_inspector: object | None = None
        self.scene_render_catalog: object | None = None
        self.overlay_settings: object | None = None
        self._paused = False
        self.instances.append(self)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def start_playback(self) -> None:
        self.started = True
        self._paused = False

    def pause_playback(self) -> None:
        self._paused = True

    def resume_playback(self) -> None:
        self._paused = False

    def retry(self) -> None:
        self.retries += 1
        self._paused = False

    def cleanup(self) -> None:
        self.cleaned = True

    def get_filter_config(self) -> None:
        return None

    def attach_filter_widget(self, filter_widget: object) -> None:
        self.filter_widget = filter_widget

    def attach_scene_inspector(self, scene_inspector: object) -> None:
        self.scene_inspector = scene_inspector

    def attach_scene_render_catalog(self, scene_render_catalog: object) -> None:
        self.scene_render_catalog = scene_render_catalog

    def set_overlay_persistence(self, overlay_settings: object) -> None:
        self.overlay_settings = overlay_settings


class TestLiveVideoViewerWidget:
    """Tests for the live video viewer."""

    def test_live_viewer_display_name(self, qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> None:
        content = _make_live_content("Camera 1")
        with patch("ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController"):
            widget = LiveVideoViewerWidget(content, render_catalog_manager=render_catalog_manager)
            qtbot.addWidget(widget)
            assert widget.get_display_name() == "Camera 1"

    def test_cleanup_calls_controller_cleanup(
        self,
        qtbot: QtBot,
        render_catalog_manager: SceneRenderCatalogManager,
    ) -> None:
        content = _make_live_content()
        with patch("ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController") as mock_ctrl_cls:
            widget = LiveVideoViewerWidget(content, render_catalog_manager=render_catalog_manager)
            qtbot.addWidget(widget)
            mock_controller = mock_ctrl_cls.return_value
            widget.cleanup()
            mock_controller.cleanup.assert_called_once()

    def test_cleanup_with_no_controller(self, qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> None:
        content = _make_live_content()
        with patch(
            "ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController",
            side_effect=Exception("fail"),
        ):
            with pytest.raises(RuntimeError, match="Failed to start live stream: fail"):
                LiveVideoViewerWidget(content, render_catalog_manager=render_catalog_manager)

    def test_replacing_side_panel_after_clear_removes_placeholder(
        self,
        qtbot: QtBot,
        render_catalog_manager: SceneRenderCatalogManager,
    ) -> None:
        """Clearing then reattaching side-panel content should not leave placeholder rows behind."""
        content = _make_live_content()
        with patch("ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController"):
            widget = LiveVideoViewerWidget(content, render_catalog_manager=render_catalog_manager)
            qtbot.addWidget(widget)

            assert widget._frame_display is not None
            side_panel_controller = widget._frame_display._side_panel_controller
            side_panel = side_panel_controller._side_panel
            assert side_panel is not None

            widget._frame_display.set_side_panel_widget(None)
            replacement = MediaToolsPanel(render_catalog_manager.create_selection())
            widget._frame_display.set_side_panel_widget(replacement)
            QCoreApplication.processEvents()

            layout = side_panel.layout()
            assert layout is not None

            layout_widgets = [
                item.widget()
                for index in range(layout.count())
                if (item := layout.itemAt(index)) is not None and item.widget() is not None
            ]
            assert layout_widgets == [replacement]

    def test_live_viewer_composes_frame_display_status_and_tools(
        self,
        qtbot: QtBot,
        render_catalog_manager: SceneRenderCatalogManager,
    ) -> None:
        content = _make_live_content()
        _FakeStreamMediaController.instances.clear()
        with patch(
            "ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController",
            _FakeStreamMediaController,
        ):
            widget = LiveVideoViewerWidget(content, render_catalog_manager=render_catalog_manager)
            qtbot.addWidget(widget)

            controller = _FakeStreamMediaController.instances[-1]
            assert controller.started
            assert widget._frame_display is controller.frame_display
            assert controller.filter_widget is not None
            assert controller.scene_inspector is not None

            frame = _make_display_frame()
            controller.frame_display.display_frame(frame)
            controller.frame_displayed(frame)

            assert widget._frame_display is not None
            assert widget._frame_display.viewport._video_frame is frame
            assert widget._frame_display.viewport._video_frame.overlays is frame.overlays
            assert widget._status_panel is not None
            assert widget._status_panel._timestamp_label.text() == "00:00:10.250000"
            assert widget._status_panel._frame_label.text() == "Frame: 42"

            widget.cleanup()

            assert controller.cleaned
            assert widget._frame_display is None

    def test_tools_toggle_opens_media_tools(
        self,
        qtbot: QtBot,
        render_catalog_manager: SceneRenderCatalogManager,
    ) -> None:
        _FakeStreamMediaController.instances.clear()
        with patch(
            "ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController",
            _FakeStreamMediaController,
        ):
            widget = LiveVideoViewerWidget(_make_live_content(), render_catalog_manager=render_catalog_manager)
            qtbot.addWidget(widget)
            assert widget._status_panel is not None and widget._frame_display is not None
            display = widget._frame_display

            assert not display.is_side_panel_open()

            widget.toggle_media_tools()
            assert display.is_side_panel_open()

            widget.toggle_media_tools()
            assert not display.is_side_panel_open()

            widget.cleanup()

    def test_pause_button_pauses_and_resumes_with_visible_status(
        self,
        qtbot: QtBot,
        render_catalog_manager: SceneRenderCatalogManager,
    ) -> None:
        content = _make_live_content()
        _FakeStreamMediaController.instances.clear()
        with patch(
            "ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController",
            _FakeStreamMediaController,
        ):
            widget = LiveVideoViewerWidget(content, render_catalog_manager=render_catalog_manager)
            qtbot.addWidget(widget)

            controller = _FakeStreamMediaController.instances[-1]
            assert not controller.is_paused
            assert widget._status_panel is not None

            button = widget._status_panel.findChild(QPushButton)
            assert button is not None
            button.click()

            assert controller.is_paused
            assert not widget._status_panel._paused_label.isHidden()

            button.click()

            assert not controller.is_paused
            assert widget._status_panel._paused_label.isHidden()

            widget.cleanup()

    def test_connection_status_shows_state_reasons_and_retry(
        self,
        qtbot: QtBot,
        render_catalog_manager: SceneRenderCatalogManager,
    ) -> None:
        """Failures are visible in the pane with their reason, per feed, and Retry reopens the stream."""
        content = _make_live_content()
        _FakeStreamMediaController.instances.clear()
        with patch(
            "ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController",
            _FakeStreamMediaController,
        ):
            widget = LiveVideoViewerWidget(content, render_catalog_manager=render_catalog_manager)
            qtbot.addWidget(widget)
            widget.show()
            controller = _FakeStreamMediaController.instances[-1]
            panel = widget._status_panel
            display = widget._frame_display
            assert panel is not None and display is not None

            assert panel._state_label.text() == "Connecting…"
            assert panel._problem_row.isHidden()
            assert display.viewport._background_text == "Connecting…\n\nWaiting for the device to answer."

            status = controller.connection_status
            status = status.with_status(status.feed(LiveFeed.VIDEO).live())
            status = status.with_status(status.feed(LiveFeed.OVERLAY).reconnecting("Connection refused"))
            controller.connection_changed(status)

            assert panel._state_label.text() == "● Live"
            assert not panel._problem_row.isHidden()
            assert panel._problem_label.text() == "Overlay: Reconnecting (1) — Connection refused"
            assert panel._retry_button.isHidden()

            status = status.with_status(status.feed(LiveFeed.VIDEO).failed("RTSP Error: Unauthorized"))
            controller.connection_changed(status)

            assert panel._state_label.text() == "✕ Failed"
            assert panel._problem_label.text().splitlines() == [
                "Video: ✕ Failed — RTSP Error: Unauthorized",
                "Overlay: Reconnecting (1) — Connection refused",
            ]
            assert display.viewport._background_text == "✕ Failed\n\nRTSP Error: Unauthorized"
            assert not panel._retry_button.isHidden()
            assert panel.height() == panel.sizeHint().height()
            assert panel.geometry().bottom() == display.viewport.height() - 1

            widget.pause_playback()
            panel._retry_button.click()

            assert controller.retries == 1
            assert panel._paused_label.isHidden()

            widget.cleanup()
