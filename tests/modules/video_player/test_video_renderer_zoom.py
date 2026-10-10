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


def _image_point_under(renderer: VideoFrameRenderer, position: QPointF) -> tuple[float, float]:
    base = renderer.frame_display_rect()
    assert base is not None
    shown = renderer.viewport_state.compute_target_rect(base)
    return ((position.x() - shown.x()) / shown.width(), (position.y() - shown.y()) / shown.height())


def _normalized_view(renderer: VideoFrameRenderer) -> NormalizedViewport:
    base = renderer.frame_display_rect()
    assert base is not None
    return renderer.viewport_state.to_normalized(base)


def test_wheel_zoom_keeps_the_image_point_under_the_cursor(renderer: VideoFrameRenderer) -> None:
    for _ in range(3):  # Fill the view first so clamping the pan to the image edges does not apply.
        renderer.zoom(ZoomStep.IN)
    cursor = QPointF(80.0, 140.0)
    before = _image_point_under(renderer, cursor)
    zoom_before = renderer.viewport_state.zoom_level

    renderer.wheelEvent(_make_wheel_event(cursor, 120))

    assert renderer.viewport_state.zoom_level > zoom_before
    assert _image_point_under(renderer, cursor) == pytest.approx(before)


def test_zooming_back_to_minimum_resets_pan(renderer: VideoFrameRenderer) -> None:
    for _ in range(3):
        renderer.wheelEvent(_make_wheel_event(QPointF(80.0, 140.0), 120))
    assert renderer.viewport_state.pan_offset != QPointF(0.0, 0.0)

    # Repeated zoom-out steps should eventually hit minimum and reset pan.
    for _ in range(16):
        renderer.wheelEvent(_make_wheel_event(QPointF(500.0, 300.0), -120))

    assert renderer.viewport_state.zoom_level == 1.0
    assert renderer.viewport_state.pan_offset == QPointF(0.0, 0.0)


def test_resize_preserves_relative_zoom_and_normalized_pan(renderer: VideoFrameRenderer) -> None:
    _show_resizable_renderer(renderer)
    renderer.set_viewport(NormalizedViewport(zoom=2.5, pan_x=0.15, pan_y=-0.1))
    before = _normalized_view(renderer)
    assert before.pan_x != 0.0 and before.pan_y != 0.0

    renderer.resize(560, 420)
    QCoreApplication.processEvents()

    assert tuple(_normalized_view(renderer)) == pytest.approx(tuple(before))


def test_resize_round_trip_restores_view_after_temporary_pan_clamp(renderer: VideoFrameRenderer) -> None:
    _show_resizable_renderer(renderer)
    renderer.set_viewport(NormalizedViewport(zoom=2.5, pan_x=0.15, pan_y=-0.1))
    before = _normalized_view(renderer)
    assert before.pan_y != 0.0

    renderer.resize(640, 1200)
    QCoreApplication.processEvents()
    assert renderer.viewport_state.pan_offset.y() == 0.0

    renderer.resize(640, 480)
    QCoreApplication.processEvents()

    assert tuple(_normalized_view(renderer)) == pytest.approx(tuple(before))


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

    renderer.wheelEvent(_make_wheel_event(QPointF(80.0, 140.0), 120))
    assert renderer.viewport_state.pan_offset != QPointF(0.0, 0.0)
    renderer.zoom(ZoomStep.RESET)
    assert renderer.viewport_state.zoom_level == 1.0
    assert renderer.viewport_state.pan_offset == QPointF(0.0, 0.0)
    assert emitted[-1] == NormalizedViewport(zoom=1.0, pan_x=0.0, pan_y=0.0)
