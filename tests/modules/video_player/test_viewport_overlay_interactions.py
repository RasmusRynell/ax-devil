"""Input ownership between the video viewport and interactive overlays."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.draggable import DraggableHandle
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay


def _make_display_with_frame(qtbot: QtBot) -> FrameDisplay:
    display = FrameDisplay()
    qtbot.addWidget(display)
    display.resize(640, 480)
    display.show()

    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    image.fill(0)
    frame = VideoFrame(image=image, timestamp=0.0, frame_id=0)
    display.display_frame(
        VideoFrameWithOverlays(
            frame=frame,
            overlays=None,
        )
    )
    QCoreApplication.processEvents()
    return display


def _zoom_viewport(display: FrameDisplay) -> None:
    display.viewport.viewport_state.zoom_level = 2.0
    display.viewport.viewport_state.pan_offset = QPointF(0.0, 0.0)


def test_dragging_interactive_overlay_does_not_pan_zoomed_viewport(qtbot: QtBot) -> None:
    display = _make_display_with_frame(qtbot)
    display.enable_side_panel()
    QCoreApplication.processEvents()
    _zoom_viewport(display)

    handle = display.viewport.findChild(DraggableHandle)
    assert handle is not None
    start = handle.rect().center()
    original_pan = QPointF(display.viewport.viewport_state.pan_offset)

    cast(Any, qtbot).mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
    cast(Any, qtbot).mouseMove(handle, QPoint(start.x() - 40, start.y()))
    cast(Any, qtbot).mouseRelease(handle, Qt.MouseButton.LeftButton, pos=QPoint(start.x() - 40, start.y()))

    assert display.viewport.viewport_state.pan_offset == original_pan
    assert not display.viewport.viewport_state.is_panning


def test_dragging_zoomed_viewport_background_still_pans(qtbot: QtBot) -> None:
    display = _make_display_with_frame(qtbot)
    _zoom_viewport(display)

    viewport = display.viewport
    start = QPoint(320, 240)

    cast(Any, qtbot).mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    cast(Any, qtbot).mouseMove(viewport, QPoint(start.x() - 40, start.y()))
    cast(Any, qtbot).mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(start.x() - 40, start.y()))

    assert viewport.viewport_state.pan_offset == QPointF(-40.0, 0.0)
    assert not viewport.viewport_state.is_panning
