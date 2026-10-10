"""Rendered evidence for catalog-independent primitive painting contracts."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from ax_devil.modules.video_player.engine.drawing import DrawingStyle, _text_block
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from tests.drawing_helpers import BoxCall, DrawCall, TextCall, prepare_calls

pytestmark = pytest.mark.usefixtures("qapp")


def _paint(primitive: DrawCall) -> QImage:
    surface = QuickSurface()
    surface.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    surface.resize(200, 100)
    surface.show()
    surface.setClearColor(QColor("transparent"))  # Only the drawing, without the video canvas.
    try:
        target = QRectF(0, 0, 200, 100)
        surface.set_overlays(prepare_calls([primitive], surface.drawing_buffer(target, 100)), 1, target, 100, None)
        return surface.grabFramebuffer()
    finally:
        surface.cleanup()
        surface.deleteLater()


def test_fill_without_stroke_preserves_subpixel_edges() -> None:
    style = DrawingStyle(pen_width=0, brush_r=255, brush_a=255)
    first = _paint(BoxCall(0.2, 0.2, 0.4, 0.4, style))
    shifted = _paint(BoxCall(0.2025, 0.2, 0.4, 0.4, style))
    assert first != shifted
    assert shifted.pixelColor(80, 40).alpha() == 255
    assert first.pixelColor(39, 40).alpha() == 0


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


def test_baseline_and_top_anchor_use_the_same_shaped_text(qapp: QApplication) -> None:
    block = _text_block("Text", "Arial", 16, 96)
    style = DrawingStyle(font_size=0.16)
    top = _paint(TextCall(0.2, 0.2, "Text", style, anchor="top-left"))
    baseline = _paint(TextCall(0.2, (20 + block.ascent) / 100, "Text", style, anchor="baseline"))
    assert top == baseline
