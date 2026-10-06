"""Zoom behavior tests for the core video renderer."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer
from ax_devil.modules.video_player.engine.viewport_state import NormalizedViewport, ZoomStep


@pytest.fixture
def renderer(qtbot: QtBot) -> VideoFrameRenderer:
    """Keep the renderer and its Python texture adapters alive through native teardown."""
    renderer = VideoFrameRenderer()
    qtbot.addWidget(renderer, before_close_func=lambda widget: widget.cleanup())
    renderer.setFixedSize(640, 480)

    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    image.fill(0)
    frame = VideoFrame(image=image, timestamp=0.0, frame_id=0)
    renderer.display_frame(
        VideoFrameWithOverlays(
            frame=frame,
            overlays=None,
        )
    )
    return renderer


def _show_resizable_renderer(renderer: VideoFrameRenderer) -> None:
    renderer.setMinimumSize(100, 100)
    renderer.setMaximumSize(16777215, 16777215)
    renderer.show()
    QCoreApplication.processEvents()


def _make_wheel_event(position: QPointF, delta_y: int) -> QWheelEvent:
    local_pos = QPointF(position)
    global_pos = QPointF(position)
    pixel_delta = QPoint(0, 0)
    angle_delta = QPoint(0, delta_y)
    return QWheelEvent(
        local_pos,
        global_pos,
        pixel_delta,
        angle_delta,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_wheel_zoom_adjusts_pan_toward_cursor(renderer: VideoFrameRenderer) -> None:
    # Zoom with cursor away from center — pan should shift toward cursor.
    renderer.wheelEvent(_make_wheel_event(QPointF(80.0, 70.0), 120))

    assert renderer.viewport_state.zoom_level > 1.0
    assert renderer.viewport_state.pan_offset != QPointF(0.0, 0.0)


def test_zooming_back_to_minimum_resets_pan(renderer: VideoFrameRenderer) -> None:
    renderer.viewport_state.zoom_level = 2.0
    renderer.viewport_state.pan_offset = QPointF(30.0, -20.0)

    # Repeated zoom-out steps should eventually hit minimum and reset pan.
    for _ in range(16):
        renderer.wheelEvent(_make_wheel_event(QPointF(100.0, 100.0), -120))

    assert renderer.viewport_state.zoom_level == 1.0
    assert renderer.viewport_state.pan_offset == QPointF(0.0, 0.0)


def test_resize_preserves_relative_zoom_and_normalized_pan(renderer: VideoFrameRenderer) -> None:
    assert renderer._video_frame is not None

    _show_resizable_renderer(renderer)
    renderer.viewport_state.zoom_level = 2.5
    renderer.viewport_state.pan_offset = QPointF(100.0, -50.0)
    base_before = renderer._base_rect(renderer._video_frame.frame.image.size())
    renderer._clamp_pan(base_before)
    normalized_before = renderer.viewport_state.normalized_pan(base_before)
    zoom_before = renderer.viewport_state.zoom_level

    renderer.resize(560, 420)
    QCoreApplication.processEvents()
    base_after = renderer._base_rect(renderer._video_frame.frame.image.size())
    normalized_after = renderer.viewport_state.normalized_pan(base_after)

    assert normalized_after == pytest.approx(normalized_before)
    assert renderer.viewport_state.zoom_level == pytest.approx(zoom_before)


def test_resize_round_trip_restores_view_after_temporary_pan_clamp(renderer: VideoFrameRenderer) -> None:
    assert renderer._video_frame is not None

    _show_resizable_renderer(renderer)
    renderer.viewport_state.zoom_level = 2.5
    renderer.viewport_state.pan_offset = QPointF(120.0, -40.0)
    base_before = renderer._base_rect(renderer._video_frame.frame.image.size())
    renderer._clamp_pan(base_before)
    zoom_before = renderer.viewport_state.zoom_level
    pan_before = QPointF(renderer.viewport_state.pan_offset)

    renderer.resize(640, 1200)
    QCoreApplication.processEvents()
    assert renderer.viewport_state.pan_offset.y() == 0.0

    renderer.resize(640, 480)
    QCoreApplication.processEvents()

    assert renderer.viewport_state.zoom_level == pytest.approx(zoom_before)
    assert renderer.viewport_state.pan_offset.x() == pytest.approx(pan_before.x())
    assert renderer.viewport_state.pan_offset.y() == pytest.approx(pan_before.y())


def test_keyboard_zoom_steps_around_center_and_resets_peers(renderer: VideoFrameRenderer) -> None:
    emitted: list[NormalizedViewport] = []
    renderer.viewportChanged.connect(emitted.append)

    renderer.zoom(ZoomStep.IN)
    one_step = renderer.viewport_state.zoom_level
    renderer.zoom(ZoomStep.IN)
    assert renderer.viewport_state.zoom_level > one_step > 1.0
    assert renderer.viewport_state.pan_offset == QPointF(0.0, 0.0)

    renderer.zoom(ZoomStep.OUT)
    assert renderer.viewport_state.zoom_level == pytest.approx(one_step)

    renderer.viewport_state.pan_offset = QPointF(10.0, 5.0)
    renderer.zoom(ZoomStep.RESET)
    assert renderer.viewport_state.zoom_level == 1.0
    assert renderer.viewport_state.pan_offset == QPointF(0.0, 0.0)
    assert emitted[-1] == NormalizedViewport(zoom=1.0, pan_x=0.0, pan_y=0.0)
