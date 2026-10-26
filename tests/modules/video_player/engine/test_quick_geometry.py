"""Rendered geometry, paths and text of the native Quick backend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtQml import QQmlProperty
from PySide6.QtWidgets import QApplication

from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from tests.drawing_helpers import (
    BoxCall,
    CircleCall,
    DrawCall,
    DrawCalls,
    DrawingStyle,
    LineCall,
    PointCall,
    PolygonCall,
    PolylineCall,
    prepare_calls,
)
from tests.helpers.quick_surface import (
    FILL,
    WHITE,
    render_calls,
    solid_image,
)


@pytest.mark.parametrize(
    "primitive,inside,outside",
    [
        (BoxCall(0.1, 0.1, 0.5, 0.5, FILL), (50, 30), (150, 30)),
        (CircleCall(0.5, 0.5, 0.2, FILL), (100, 50), (140, 50)),
        (
            PolygonCall(((0.1, 0.1), (0.9, 0.1), (0.9, 0.3), (0.3, 0.3), (0.3, 0.9), (0.1, 0.9)), FILL),
            (30, 70),
            (140, 70),
        ),
        (LineCall(0.1, 0.5, 0.9, 0.5, WHITE), (100, 50), (100, 60)),
        (PointCall(0.5, 0.5, WHITE), (100, 50), (110, 50)),
        (PolylineCall(((0.1, 0.2), (0.5, 0.2), (0.5, 0.8)), WHITE), (100, 60), (60, 60)),
    ],
)
def test_general_geometry_has_expected_coverage(
    surface: QuickSurface,
    primitive: DrawCall,
    inside: tuple[int, int],
    outside: tuple[int, int],
) -> None:
    actual = render_calls(surface, [primitive])
    assert actual.pixelColor(*inside) != QColor("black")
    assert actual.pixelColor(*outside) == QColor("black")


def test_order_opacity_and_removal(surface: QuickSurface) -> None:
    blue = DrawingStyle(pen_width=0, brush_b=255, brush_a=255)
    primitives: DrawCalls = [BoxCall(0.1, 0.1, 0.6, 0.6, FILL), BoxCall(0.3, 0.3, 0.6, 0.6, blue)]
    actual = render_calls(surface, primitives, 0.5)
    for point, expected in (
        ((40, 20), QColor(128, 0, 0)),
        ((100, 50), QColor(0, 0, 128)),
        ((170, 80), QColor(0, 0, 128)),
    ):
        color = actual.pixelColor(*point)
        assert abs(color.blue() - expected.blue()) <= 2
        assert abs(color.red() - expected.red()) <= (64 if point == (100, 50) else 2)
    actual = render_calls(surface, [primitives[0]])
    assert actual.pixelColor(100, 50) == QColor("red")
    assert actual.pixelColor(170, 80) == QColor("black")
    assert render_calls(surface, []).pixelColor(40, 20) == QColor("black")


def test_a_faded_overlay_is_as_dark_at_a_join_as_along_a_segment(surface: QuickSurface) -> None:
    """Overlapping quads of one stroke fade together, so joins show no darker dots."""
    stroke = DrawingStyle(pen_r=255, pen_width=0.1)
    line = PolylineCall(((0.2, 0.5), (0.5, 0.5), (0.8, 0.5)), stroke)
    faded = render_calls(surface, [line], 0.5)
    assert faded.pixelColor(100, 50) == faded.pixelColor(70, 50)
    assert faded.pixelColor(70, 50) != render_calls(surface, [], 0.5).pixelColor(70, 50)


def test_a_faded_overlay_at_deep_zoom_renders_only_the_viewport(surface: QuickSurface) -> None:
    """The overlay item spans from the zoomed image's origin, so its fade layer must stay the size of the view."""
    target = QRectF(-1800, -900, 2000, 1000)  # A 10x zoom of the 200x100 view, panned to its middle.
    surface.set_frame_image(solid_image(), target)
    prepared = prepare_calls([BoxCall(0.0, 0.0, 1.0, 1.0, FILL)], surface.drawing_buffer(target, 1000))
    surface.set_overlays(prepared, 0.5, target, 1000, None)
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50).red() > 100
    layer = QQmlProperty(surface._overlay, "layer.sourceRect").read()
    assert (layer.width(), layer.height()) == (surface.width(), surface.height())


def test_dash_zero_stroke_and_zero_area(surface: QuickSurface) -> None:
    style = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=0.03, pen_style="dash")
    actual = render_calls(surface, [LineCall(0.1, 0.5, 0.9, 0.5, style)])
    colors = [actual.pixelColor(x, 50).red() for x in range(25, 175)]
    assert max(colors) == 255 and min(colors) == 0
    invisible: DrawCalls = [
        BoxCall(0.1, 0.1, 0.5, 0.5, DrawingStyle(pen_width=0)),
        BoxCall(0.2, 0.2, 0, 0.5),
        BoxCall(0.2, 0.2, 0.5, 0),
        CircleCall(0.5, 0.5, 0),
        PointCall(0.5, 0.5, DrawingStyle(pen_width=0)),
    ]
    assert render_calls(surface, invisible).convertToFormat(QImage.Format.Format_RGB32) == solid_image()


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
        actual = render_calls(surface, [primitive], opacity).convertToFormat(QImage.Format.Format_RGBA8888)
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
