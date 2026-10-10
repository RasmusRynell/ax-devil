"""Tests for the live-only ``LiveVideoViewerWidget``."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from unittest.mock import patch

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_player import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.quick.preparation import PreparedDrawing
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.live_connection import LiveConnectionStatus, LiveFeed
from ax_devil.modules.video_viewer.live_video_viewer import LiveStatusPanel, LiveVideoViewerWidget
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
        self.scene_filter: object | None = None
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

    def attach_scene_filter(self, scene_filter: object) -> None:
        self.scene_filter = scene_filter

    def attach_scene_inspector(self, scene_inspector: object) -> None:
        self.scene_inspector = scene_inspector

    def attach_scene_render_catalog(self, scene_render_catalog: object) -> None:
        self.scene_render_catalog = scene_render_catalog

    def set_overlay_persistence(self, overlay_settings: object) -> None:
        self.overlay_settings = overlay_settings


_CONTROLLER = "ax_devil.modules.video_viewer.live_video_viewer.StreamMediaController"


@pytest.fixture
def live_viewer(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> Iterator[tuple[LiveVideoViewerWidget, _FakeStreamMediaController]]:
    """Open a live viewer on a fake controller and clean it up after the test."""
    _FakeStreamMediaController.instances.clear()
    with patch(_CONTROLLER, _FakeStreamMediaController):
        widget = LiveVideoViewerWidget(_make_live_content(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(widget)
    yield widget, _FakeStreamMediaController.instances[-1]
    widget.cleanup()


def _label_texts(widget: QWidget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel) if label.isVisibleTo(widget)]


def _display(widget: LiveVideoViewerWidget) -> FrameDisplay:
    display = widget.findChild(FrameDisplay)
    assert display is not None
    return display


def test_live_viewer_reports_stream_start_failure(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    with patch(_CONTROLLER, side_effect=Exception("fail")):
        with pytest.raises(RuntimeError, match="Failed to start live stream: fail"):
            LiveVideoViewerWidget(_make_live_content(), render_catalog_manager=render_catalog_manager)


def test_live_viewer_shows_displayed_frame_status_and_releases_controller(
    live_viewer: tuple[LiveVideoViewerWidget, _FakeStreamMediaController],
) -> None:
    widget, controller = live_viewer
    assert controller.started
    assert controller.frame_display is _display(widget)
    assert controller.scene_filter is not None
    assert controller.scene_inspector is not None

    frame = _make_display_frame()
    controller.frame_displayed(frame)

    texts = _label_texts(widget)
    assert "00:00:10.250000" in texts
    assert "Frame: 42" in texts

    widget.cleanup()

    assert controller.cleaned
    assert widget.findChild(FrameDisplay) is None


def test_tools_toggle_opens_media_tools(
    live_viewer: tuple[LiveVideoViewerWidget, _FakeStreamMediaController],
) -> None:
    widget, _controller = live_viewer
    display = _display(widget)
    assert not display.is_side_panel_open()

    widget.toggle_media_tools()
    assert display.is_side_panel_open()

    widget.toggle_media_tools()
    assert not display.is_side_panel_open()


def test_pause_button_pauses_and_resumes_with_visible_status(
    live_viewer: tuple[LiveVideoViewerWidget, _FakeStreamMediaController],
) -> None:
    widget, controller = live_viewer
    panel = widget.findChild(LiveStatusPanel)
    assert panel is not None
    button = panel.findChild(QPushButton)
    assert button is not None

    button.click()

    assert controller.is_paused
    assert "PAUSED" in _label_texts(widget)

    button.click()

    assert not controller.is_paused
    assert "PAUSED" not in _label_texts(widget)


def test_connection_status_shows_state_reasons_and_retry(
    live_viewer: tuple[LiveVideoViewerWidget, _FakeStreamMediaController],
) -> None:
    """Failures are visible in the pane with their reason, per feed, and Retry reopens the stream."""
    widget, controller = live_viewer
    widget.show()
    panel = widget.findChild(LiveStatusPanel)
    assert panel is not None
    retry = next(button for button in panel.findChildren(QPushButton) if button.text() == "Retry")
    viewport = _display(widget).viewport

    def problem_text() -> str:
        return "\n".join(_label_texts(panel))

    assert retry.isHidden()

    status = controller.connection_status
    status = status.with_status(status.feed(LiveFeed.VIDEO).live())
    status = status.with_status(status.feed(LiveFeed.OVERLAY).reconnecting("Connection refused"))
    controller.connection_changed(status)

    assert "Connection refused" in problem_text()
    assert retry.isHidden()

    status = status.with_status(status.feed(LiveFeed.VIDEO).failed("RTSP Error: Unauthorized"))
    controller.connection_changed(status)

    assert "RTSP Error: Unauthorized" in problem_text()
    assert "Connection refused" in problem_text()
    assert "RTSP Error: Unauthorized" in viewport._background_text  # noqa: SLF001 - only painted, no accessor
    assert not retry.isHidden()
    # The grown status panel must stay fully visible at the bottom of the video.
    assert panel.height() >= panel.sizeHint().height()
    assert panel.geometry().bottom() == viewport.height() - 1

    widget.pause_playback()
    retry.click()

    assert controller.retries == 1
    assert not controller.is_paused
    assert "PAUSED" not in _label_texts(widget)
