"""Rendered-output and lifecycle contracts for the native Quick backend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen
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
    CircleCall,
    DrawCall,
    DrawCalls,
    DrawingStyle,
    LabelCall,
    LabelContent,
    LabelRun,
    LineCall,
    PointCall,
    PolygonCall,
    PolylineCall,
    TextCall,
    prepare_calls,
)

_FILL = DrawingStyle(pen_width=0, brush_r=255, brush_g=0, brush_b=0, brush_a=255)
_WHITE = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=0.04, font_size=0.18)
_TARGET = QRectF(0, 0, 200, 100)


def _image(color: str = "black") -> QImage:
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def _frame(image: QImage, primitives: DrawCalls, opacity: float = 1.0) -> VideoFrameWithOverlays:
    return VideoFrameWithOverlays(
        VideoFrame(image, 0),
        VideoOverlayData(
            lambda _context, buffer: prepare_calls(primitives, buffer), 0, metadata={"overlay_opacity": opacity}
        ),
    )


@pytest.fixture
def surface(qtbot: QtBot) -> QuickSurface:
    """Create a real scene graph surface on the test display."""
    return _surface(qtbot)


@pytest.fixture
def other_surface(qtbot: QtBot) -> QuickSurface:
    """Retain the second viewer through QtBot's native widget teardown."""
    return _surface(qtbot)


def _surface(qtbot: QtBot) -> QuickSurface:
    widget = QuickSurface()
    qtbot.addWidget(widget, before_close_func=lambda widget: widget.cleanup())
    widget.resize(200, 100)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def _render(surface: QuickSurface, primitives: DrawCalls, opacity: float = 1.0) -> QImage:
    surface.set_frame_image(_image(), _TARGET)
    surface.set_overlays(prepare_calls(primitives, surface.drawing_buffer(_TARGET, 100)), opacity, _TARGET, 100, None)
    QApplication.processEvents()
    return surface.grabFramebuffer()


@pytest.mark.parametrize(
    "primitive,inside,outside",
    [
        (BoxCall(0.1, 0.1, 0.5, 0.5, _FILL), (50, 30), (150, 30)),
        (CircleCall(0.5, 0.5, 0.2, _FILL), (100, 50), (140, 50)),
        (
            PolygonCall(((0.1, 0.1), (0.9, 0.1), (0.9, 0.3), (0.3, 0.3), (0.3, 0.9), (0.1, 0.9)), _FILL),
            (30, 70),
            (140, 70),
        ),
        (LineCall(0.1, 0.5, 0.9, 0.5, _WHITE), (100, 50), (100, 60)),
        (PointCall(0.5, 0.5, _WHITE), (100, 50), (110, 50)),
        (PolylineCall(((0.1, 0.2), (0.5, 0.2), (0.5, 0.8)), _WHITE), (100, 60), (60, 60)),
    ],
)
def test_general_geometry_has_expected_coverage(
    surface: QuickSurface,
    primitive: DrawCall,
    inside: tuple[int, int],
    outside: tuple[int, int],
) -> None:
    actual = _render(surface, [primitive])
    assert actual.pixelColor(*inside) != QColor("black")
    assert actual.pixelColor(*outside) == QColor("black")


def test_order_opacity_and_removal(surface: QuickSurface) -> None:
    blue = DrawingStyle(pen_width=0, brush_b=255, brush_a=255)
    primitives: DrawCalls = [BoxCall(0.1, 0.1, 0.6, 0.6, _FILL), BoxCall(0.3, 0.3, 0.6, 0.6, blue)]
    actual = _render(surface, primitives, 0.5)
    for point, expected in (
        ((40, 20), QColor(128, 0, 0)),
        ((100, 50), QColor(0, 0, 128)),
        ((170, 80), QColor(0, 0, 128)),
    ):
        color = actual.pixelColor(*point)
        assert abs(color.blue() - expected.blue()) <= 2
        assert abs(color.red() - expected.red()) <= (64 if point == (100, 50) else 2)
    actual = _render(surface, [primitives[0]])
    assert actual.pixelColor(100, 50) == QColor("red")
    assert actual.pixelColor(170, 80) == QColor("black")
    assert _render(surface, []).pixelColor(40, 20) == QColor("black")


def test_multiline_text_draws_above_geometry_in_any_emission_order(surface: QuickSurface) -> None:
    text = TextCall(0.1, 0.1, "Hello\nWorld", _WHITE, anchor="top-left")
    rendered = _render(surface, [text])
    lit_top = sum(rendered.pixelColor(x, y).red() > 100 for y in range(10, 30) for x in range(20, 100))
    lit_bottom = sum(rendered.pixelColor(x, y).red() > 100 for y in range(30, 60) for x in range(20, 100))
    assert lit_top > 30
    assert lit_bottom > 30
    box = BoxCall(0, 0, 1, 1, _FILL)
    orders: list[DrawCalls] = [[text, box], [box, text]]
    for calls in orders:
        visible = _render(surface, calls)
        assert any(visible.pixelColor(x, y).green() > 100 for y in range(10, 60) for x in range(20, 100))
        assert visible.pixelColor(190, 90) == QColor("red")


def test_dash_zero_stroke_and_zero_area(surface: QuickSurface) -> None:
    style = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=0.03, pen_style="dash")
    actual = _render(surface, [LineCall(0.1, 0.5, 0.9, 0.5, style)])
    colors = [actual.pixelColor(x, 50).red() for x in range(25, 175)]
    assert max(colors) == 255 and min(colors) == 0
    invisible: DrawCalls = [
        BoxCall(0.1, 0.1, 0.5, 0.5, DrawingStyle(pen_width=0)),
        BoxCall(0.2, 0.2, 0, 0.5),
        BoxCall(0.2, 0.2, 0.5, 0),
        CircleCall(0.5, 0.5, 0),
        PointCall(0.5, 0.5, DrawingStyle(pen_width=0)),
    ]
    assert _render(surface, invisible).convertToFormat(QImage.Format.Format_RGB32) == _image()


def test_texture_replacement_resize_reparent_and_clear(surface: QuickSurface, qtbot: QtBot) -> None:
    for color in ("red", "green", "blue", "white"):
        surface.set_frame_image(_image(color), _TARGET)
        QApplication.processEvents()
        assert surface.grabFramebuffer().pixelColor(100, 50) == QColor(color)
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(300, 200)
    surface.setParent(host)
    surface.resize(300, 200)
    surface.set_frame_image(_image("green"), QRectF(0, 0, 300, 200))
    host.show()
    surface.show()
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(250, 150) == QColor("green")
    surface.clear()
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50) == VIDEO_CANVAS
    surface.setParent(None)


def test_letterbox_stays_video_canvas_when_theme_changes(surface: QuickSurface) -> None:
    surface.set_frame_image(_image("green"), QRectF(50, 0, 100, 100))
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(10, 50) == VIDEO_CANVAS
    palette = surface.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("magenta"))
    surface.setPalette(palette)
    surface.set_frame_image(_image("green"), QRectF(50, 0, 100, 100))
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
            return prepare_calls([BoxCall(0, 0, 0.25 if color == "green" else 1, 1, _FILL)], buffer)

        return VideoFrameWithOverlays(VideoFrame(_image(color), 0), VideoOverlayData(draw, 0))

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


def test_moving_text_updates_position_and_replaces_glyphs(surface: QuickSurface) -> None:
    text = TextCall(0.1, 0.1, "Wide", _WHITE, anchor="top-left")
    first = _render(surface, [text])
    text = replace(text, x=0.6)
    moved = _render(surface, [text])
    assert any(first.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(20, 70))
    assert all(moved.pixelColor(x, y) == QColor("black") for y in range(10, 35) for x in range(20, 70))
    assert any(moved.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(120, 175))
    text = replace(text, text="Other")
    assert _render(surface, [text]) != moved


def test_video_texture_handles_alpha_size_changes_and_latest_upload(surface: QuickSurface) -> None:
    surface.setClearColor(QColor("transparent"))  # Read the texture's own alpha, unblended with the canvas.
    for size, alpha in ((200, False), (200, True), (64, True), (200, False)):
        image = QImage(size, size, QImage.Format.Format_RGBA8888 if alpha else QImage.Format.Format_RGB888)
        image.fill(QColor(180, 20, 40, 128 if alpha else 255))
        surface.set_frame_image(_image("blue"), _TARGET)
        surface.set_frame_image(image, _TARGET)
        QApplication.processEvents()
        actual = surface.grabFramebuffer().pixelColor(100, 50)
        assert abs(actual.red() - 180) <= 2
        assert abs(actual.green() - 20) <= 2
        assert abs(actual.blue() - 40) <= 2
        assert abs(actual.alpha() - (128 if alpha else 255)) <= 2


def test_video_storage_is_retained_for_same_size_frames(surface: QuickSurface) -> None:
    surface.set_frame_image(_image("red"), _TARGET)
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50) == QColor("red")
    texture = surface._video._texture
    for color in ("blue", "green", "white"):
        surface.set_frame_image(_image(color), _TARGET)
        QApplication.processEvents()
        assert surface.grabFramebuffer().pixelColor(100, 50) == QColor(color)
        assert surface._video._texture is texture
    surface.cleanup()
    if texture is not None:
        assert not isValid(texture)


def _box(painter: QPainter, call: BoxCall) -> None:
    pen = painter.pen()
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    painter.setPen(pen)
    painter.drawRect(QRectF(call.x * 200, call.y * 100, call.w * 200, call.h * 100))


def _line(painter: QPainter, call: LineCall) -> None:
    painter.drawLine(QPointF(call.x1 * 200, call.y1 * 100), QPointF(call.x2 * 200, call.y2 * 100))


def _polygon(painter: QPainter, call: PolygonCall) -> None:
    pen = painter.pen()
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolygon([QPointF(x * 200, y * 100) for x, y in call.points])


_OPACITY: dict[type, float] = {PolygonCall: 1.0}
_REFERENCE_DRAWERS: dict[type, Callable[..., None]] = {
    BoxCall: _box,
    LineCall: _line,
    PolygonCall: _polygon,
}


def _reference(primitive: BoxCall | LineCall | PolygonCall, opacity: float) -> QImage:
    """Draw a primitive with QPainter over black, the way Qt's own path renderer drew it."""
    image = QImage(200, 100, QImage.Format.Format_RGBA8888)
    image.fill(QColor("black"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(opacity)
    style = primitive.style
    painter.setBrush(QColor(style.brush_r, style.brush_g, style.brush_b, style.brush_a))
    width = style.pen_width * 100
    painter.setPen(QPen(QColor(style.pen_r, style.pen_g, style.pen_b), width) if width else Qt.PenStyle.NoPen)
    _REFERENCE_DRAWERS[type(primitive)](painter, primitive)
    painter.end()
    return image


@pytest.mark.parametrize("stroke", [0.005, 0.02, 0.06])
@pytest.mark.parametrize("alpha", [15, 255])
def test_numeric_geometry_preserves_painter_coverage(surface: QuickSurface, stroke: float, alpha: int) -> None:
    """Compare fractional, translucent and reversed geometry with QPainter, one primitive at a time.

    Edge coverage is approximate, but interiors, bounds and opacity must agree. Order within the
    geometry layer is not preserved, so overlapping primitives are not compared together.
    """
    style = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=stroke, brush_g=180, brush_a=alpha)
    primitives: list[BoxCall | LineCall | PolygonCall] = [
        # A box's shader stroke replaces its fill where Qt's draws over it, so only thin strokes fit the reference.
        BoxCall(0.10125, 0.1025, 0.35, 0.35, replace(style, pen_width=min(stroke, 0.02))),
        BoxCall(0.35, 0.2, 0.2, 0.6, replace(style, pen_width=0)),
        LineCall(0.9, 0.9, 0.6, 0.2, style),
        LineCall(0.05, 0.6, 0.4, 0.6, style),
        LineCall(0.55, 0.1, 0.55, 0.4, style),
        PolygonCall(((0.62, 0.62), (0.95, 0.66), (0.9, 0.95), (0.66, 0.9)), style),
        PolygonCall(((0.1, 0.95), (0.2, 0.7), (0.3, 0.95)), replace(style, pen_width=0)),
    ]
    for primitive in primitives:
        # Segment quads overlap at joins, which a translucent overlay shows, so polygons are compared opaque.
        opacity = _OPACITY.get(type(primitive), 0.6)
        actual = _render(surface, [primitive], opacity).convertToFormat(QImage.Format.Format_RGBA8888)
        expected = _reference(primitive, opacity)
        shape = (actual.height(), actual.width(), 4)
        actual_pixels = np.frombuffer(actual.constBits(), dtype=np.uint8).reshape(shape).astype(np.int16)
        expected_pixels = np.frombuffer(expected.constBits(), dtype=np.uint8).reshape(shape).astype(np.int16)
        difference = np.abs(actual_pixels - expected_pixels)
        # The shader's analytic edge differs from Qt's rasterizer by up to about 0.4 coverage. Keep differences
        # confined to boundaries.
        assert difference.max() <= 100, f"{primitive}"
        assert difference.mean() < 0.8
        uniform = np.ones(shape[:2], dtype=np.bool_)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                neighbor = np.roll(expected_pixels, (dy, dx), axis=(0, 1))
                uniform &= np.max(np.abs(expected_pixels - neighbor), axis=2) <= 2
        assert difference[uniform].max() <= 2


def test_empty_geometry_can_become_visible(surface: QuickSurface) -> None:
    """An initially empty batch must not leave a stale zero-vertex scene-graph node."""
    empty = DrawingStyle(pen_width=0, brush_a=0)
    first = _render(surface, [BoxCall(0.1, 0.1, 0.5, 0.5, empty)])
    assert first.pixelColor(50, 30) == QColor("black")
    second = _render(surface, [BoxCall(0.1, 0.1, 0.5, 0.5, _FILL)])
    assert second.pixelColor(50, 30) == QColor("red")


def test_label_draws_its_background_above_geometry_and_moves(surface: QuickSurface) -> None:
    content = LabelContent((LabelRun("Person", (255, 255, 255), "semibold"),), 0.12, background=(0, 0, 255, 255))
    label = LabelCall(0.1, 0.1, "top-left", replace(content, padding_x=0.06, padding_y=0.04))
    rendered = _render(surface, [BoxCall(0, 0, 1, 1, _FILL), label])
    assert rendered.pixelColor(22, 12) == QColor("blue")
    assert rendered.pixelColor(190, 90) == QColor("red")
    moved = _render(surface, [replace(label, x=0.5)])
    assert moved.pixelColor(22, 12) == QColor("black")
    assert moved.pixelColor(102, 12) == QColor("blue")


@pytest.mark.parametrize("origin", [(20, 10), (-20, -10)])
@pytest.mark.parametrize("opacity", [1.0, 0.5])
def test_new_and_returning_labels_keep_viewport_transform_and_opacity(
    surface: QuickSurface, origin: tuple[int, int], opacity: float
) -> None:
    """Changing label content after rendering preserves the video origin and overlay opacity."""
    content = LabelContent(
        (LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_x=0.06, padding_y=0.04
    )
    target = _TARGET.translated(*origin)
    for text in ("A", "B", "A"):
        calls: DrawCalls = [
            BoxCall(0.4, 0.4, 0.2, 0.2, _FILL),
            LabelCall(0.4, 0.4, "top-left", replace(content, runs=(LabelRun(text, (255, 255, 255)),))),
        ]
        surface.set_frame_image(_image(), target)
        surface.set_overlays(prepare_calls(calls, surface.drawing_buffer(target, 100)), opacity, target, 100, None)
        QApplication.processEvents()
        rendered = surface.grabFramebuffer()
        dpr = rendered.devicePixelRatio()
        color = rendered.pixelColor(round((82 + origin[0]) * dpr), round((42 + origin[1]) * dpr))
        assert abs(color.blue() - round(255 * opacity)) <= 1
        assert abs(color.red() - round(255 * opacity * (1 - opacity))) <= 1


def test_labels_survive_window_reparenting_and_graph_recreation(surface: QuickSurface, qtbot: QtBot) -> None:
    """Label layers belong to one node tree and are rebuilt for the new window's graphics resources."""
    label = LabelCall(
        0.1, 0.1, "top-left", LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255))
    )
    _render(surface, [label])
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(200, 100)
    surface.setParent(host)
    host.show()
    surface.show()
    assert _render(surface, [label]).pixelColor(21, 12) == QColor("blue")
    surface.cleanup()
    QApplication.processEvents()
    surface.show()
    surface.grabFramebuffer()
    assert _render(surface, [label]).pixelColor(21, 12) == QColor("blue")
    surface.setParent(None)
