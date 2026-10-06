"""Rendered evidence for catalog-independent primitive painting contracts."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication

from ax_devil.modules.video_player.engine.drawing import DrawingStyle, _get_font, _text_block
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from tests.drawing_helpers import BoxCall, DrawCall, PolygonCall, TextCall, prepare_calls

pytestmark = pytest.mark.usefixtures("qapp")


def _paint(primitive: DrawCall) -> QImage:
    surface = QuickSurface()
    surface.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    palette = surface.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("transparent"))
    surface.setPalette(palette)
    surface.resize(200, 100)
    surface.show()
    try:
        target = QRectF(0, 0, 200, 100)
        surface.set_overlays(prepare_calls([primitive], surface.drawing_buffer(target, 100)), 1, target, 100, None)
        return surface.grabFramebuffer()
    finally:
        surface.cleanup()
        surface.deleteLater()


def test_zero_stroke_does_not_create_a_hairline() -> None:
    image = _paint(BoxCall(0.2, 0.2, 0.4, 0.4, DrawingStyle(pen_width=0)))
    assert all(image.pixelColor(x, y).alpha() == 0 for y in range(100) for x in range(200))


def test_fill_without_stroke_preserves_subpixel_edges() -> None:
    style = DrawingStyle(pen_width=0, brush_r=255, brush_a=255)
    first = _paint(BoxCall(0.2, 0.2, 0.4, 0.4, style))
    shifted = _paint(BoxCall(0.2025, 0.2, 0.4, 0.4, style))
    assert first != shifted
    assert shifted.pixelColor(80, 40).alpha() == 255
    assert first.pixelColor(39, 40).alpha() == 0


@pytest.mark.parametrize("width,height", [(0, 0.4), (0.4, 0)])
def test_zero_area_box_does_not_draw_a_stroke(width: float, height: float) -> None:
    image = _paint(BoxCall(0.2, 0.2, width, height))
    assert all(image.pixelColor(x, y).alpha() == 0 for y in range(100) for x in range(200))


def test_crossing_polygon_with_zero_signed_area_still_fills() -> None:
    style = DrawingStyle(pen_width=0, brush_r=255, brush_a=255)
    image = _paint(PolygonCall(((0.2, 0.2), (0.8, 0.8), (0.2, 0.8), (0.8, 0.2)), style))
    assert image.pixelColor(100, 30).alpha() == 255
    assert image.pixelColor(100, 70).alpha() == 255
    assert image.pixelColor(50, 50).alpha() == 0


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r", "\u2028", "\u2029"])
def test_text_line_breaks_have_identical_layout(qapp: QApplication, separator: str) -> None:
    style = DrawingStyle(font_size=0.16)
    expected = _paint(TextCall(0.2, 0.2, "First\n\nLast", style, anchor="top-left"))
    actual = _paint(TextCall(0.2, 0.2, f"First{separator}{separator}Last", style, anchor="top-left"))
    assert actual == expected


def test_empty_text_lines_contribute_to_block_height(qapp: QApplication) -> None:
    single = _text_block("Text", "Arial", 16, 96)
    multiple = _text_block("Text\n\n", "Arial", 16, 96)
    assert multiple.width == single.width
    assert multiple.height > single.height * 2
    assert len(multiple.layouts) == 3


def test_font_has_no_hidden_size_offset(qapp: QApplication) -> None:
    assert _get_font("Arial", 8.5, 96).pointSizeF() == pytest.approx(8.5 * 72 / 96)
    assert _get_font("Arial", 8.5, 192).pointSizeF() == pytest.approx(8.5 * 72 / 192)


def test_baseline_and_top_anchor_use_the_same_shaped_text(qapp: QApplication) -> None:
    block = _text_block("Text", "Arial", 16, 96)
    style = DrawingStyle(font_size=0.16)
    top = _paint(TextCall(0.2, 0.2, "Text", style, anchor="top-left"))
    baseline = _paint(TextCall(0.2, (20 + block.ascent) / 100, "Text", style, anchor="baseline"))
    assert top == baseline
