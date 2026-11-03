"""Video texture, culling and surface lifecycle contracts of the native Quick backend."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.chrome.theme import VIDEO_CANVAS
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, PreparedDrawing
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from ax_devil.modules.video_player.engine.render_context import RenderContext
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from tests.drawing_helpers import (
    BoxCall,
    DrawingStyle,
    prepare_calls,
)
from tests.helpers.quick_surface import (
    FILL,
    TARGET,
    render_calls,
    solid_image,
)


def test_texture_replacement_resize_reparent_and_clear(surface: QuickSurface, qtbot: QtBot) -> None:
    for color in ("red", "green", "blue", "white"):
        surface.set_frame_image(solid_image(color), TARGET)
        QApplication.processEvents()
        assert surface.grabFramebuffer().pixelColor(100, 50) == QColor(color)
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(300, 200)
    surface.setParent(host)
    surface.resize(300, 200)
    surface.set_frame_image(solid_image("green"), QRectF(0, 0, 300, 200))
    host.show()
    surface.show()
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(250, 150) == QColor("green")
    surface.clear()
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50) == VIDEO_CANVAS
    surface.setParent(None)


def test_letterbox_stays_video_canvas_when_theme_changes(surface: QuickSurface) -> None:
    surface.set_frame_image(solid_image("green"), QRectF(50, 0, 100, 100))
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(10, 50) == VIDEO_CANVAS
    palette = surface.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("magenta"))
    surface.setPalette(palette)
    surface.set_frame_image(solid_image("green"), QRectF(50, 0, 100, 100))
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(10, 50) == VIDEO_CANVAS
    assert surface.grabFramebuffer().pixelColor(100, 50) == QColor("green")


def test_hidden_viewport_prepares_only_the_latest_frame_and_overlay_when_shown(qtbot: QtBot) -> None:
    stack = QStackedWidget()
    stack.resize(200, 100)
    widget = FrameViewport()
    qtbot.addWidget(stack, before_close_func=lambda _stack: widget.cleanup())
    stack.addWidget(widget)
    stack.addWidget(QWidget())
    stack.show()
    prepared: list[str] = []

    def frame(color: str) -> VideoFrameWithOverlays:
        def draw(_context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
            prepared.append(color)
            return prepare_calls([BoxCall(0, 0, 0.25 if color == "green" else 1, 1, FILL)], buffer)

        return VideoFrameWithOverlays(VideoFrame(solid_image(color), 0), VideoOverlayData(draw, 0))

    widget.display_frame(frame("blue"))
    stack.setCurrentIndex(1)  # Cancel preparation already queued before changing tabs.
    QApplication.processEvents()
    widget.display_frame(frame("white"))
    widget.display_frame(frame("green"))
    QApplication.processEvents()
    assert prepared == []

    stack.setCurrentIndex(0)
    QApplication.processEvents()
    surface = widget.findChild(QuickSurface)
    assert surface is not None
    image = surface.grabFramebuffer()
    assert prepared == ["green"]
    assert image.pixelColor(100, 50) == QColor("green")
    assert image.pixelColor(20, 50) == QColor("red")


def test_video_texture_handles_alpha_size_changes_and_latest_upload(surface: QuickSurface) -> None:
    surface.setClearColor(QColor("transparent"))  # Read the texture's own alpha, unblended with the canvas.
    for size, alpha in ((200, False), (200, True), (64, True), (200, False)):
        image = QImage(size, size, QImage.Format.Format_RGBA8888 if alpha else QImage.Format.Format_RGB888)
        image.fill(QColor(180, 20, 40, 128 if alpha else 255))
        surface.set_frame_image(solid_image("blue"), TARGET)
        surface.set_frame_image(image, TARGET)
        QApplication.processEvents()
        actual = surface.grabFramebuffer().pixelColor(100, 50)
        assert abs(actual.red() - 180) <= 2
        assert abs(actual.green() - 20) <= 2
        assert abs(actual.blue() - 40) <= 2
        assert abs(actual.alpha() - (128 if alpha else 255)) <= 2


def test_video_storage_is_retained_for_same_size_frames(surface: QuickSurface) -> None:
    surface.set_frame_image(solid_image("red"), TARGET)
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50) == QColor("red")
    texture = surface._video._texture
    for color in ("blue", "green", "white"):
        surface.set_frame_image(solid_image(color), TARGET)
        QApplication.processEvents()
        assert surface.grabFramebuffer().pixelColor(100, 50) == QColor(color)
        assert surface._video._texture is texture
    surface.cleanup()
    if texture is not None:
        assert not isValid(texture)


def test_empty_geometry_can_become_visible(surface: QuickSurface) -> None:
    """An initially empty batch must not leave a stale zero-vertex scene-graph node."""
    empty = DrawingStyle(pen_width=0, brush_a=0)
    first = render_calls(surface, [BoxCall(0.1, 0.1, 0.5, 0.5, empty)])
    assert first.pixelColor(50, 30) == QColor("black")
    second = render_calls(surface, [BoxCall(0.1, 0.1, 0.5, 0.5, FILL)])
    assert second.pixelColor(50, 30) == QColor("red")
