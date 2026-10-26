"""Text and label contracts of the native Quick backend."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from tests.drawing_helpers import (
    BoxCall,
    DrawCalls,
    LabelCall,
    LabelContent,
    LabelRun,
    TextCall,
    prepare_calls,
)
from tests.helpers.quick_surface import (
    FILL,
    TARGET,
    WHITE,
    render_calls,
    solid_image,
)


def test_multiline_text_draws_above_geometry_in_any_emission_order(surface: QuickSurface) -> None:
    text = TextCall(0.1, 0.1, "Hello\nWorld", WHITE, anchor="top-left")
    rendered = render_calls(surface, [text])
    lit_top = sum(rendered.pixelColor(x, y).red() > 100 for y in range(10, 30) for x in range(20, 100))
    lit_bottom = sum(rendered.pixelColor(x, y).red() > 100 for y in range(30, 60) for x in range(20, 100))
    assert lit_top > 30
    assert lit_bottom > 30
    box = BoxCall(0, 0, 1, 1, FILL)
    orders: list[DrawCalls] = [[text, box], [box, text]]
    for calls in orders:
        visible = render_calls(surface, calls)
        assert any(visible.pixelColor(x, y).green() > 100 for y in range(10, 60) for x in range(20, 100))
        assert visible.pixelColor(190, 90) == QColor("red")


def test_moving_text_updates_position_and_replaces_glyphs(surface: QuickSurface) -> None:
    text = TextCall(0.1, 0.1, "Wide", WHITE, anchor="top-left")
    first = render_calls(surface, [text])
    text = replace(text, x=0.6)
    moved = render_calls(surface, [text])
    assert any(first.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(20, 70))
    assert all(moved.pixelColor(x, y) == QColor("black") for y in range(10, 35) for x in range(20, 70))
    assert any(moved.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(120, 175))
    text = replace(text, text="Other")
    assert render_calls(surface, [text]) != moved


def test_label_draws_its_background_above_geometry_and_moves(surface: QuickSurface) -> None:
    content = LabelContent((LabelRun("Person", (255, 255, 255), "semibold"),), 0.12, background=(0, 0, 255, 255))
    label = LabelCall(0.1, 0.1, "top-left", replace(content, padding_x=0.06, padding_y=0.04))
    rendered = render_calls(surface, [BoxCall(0, 0, 1, 1, FILL), label])
    assert rendered.pixelColor(22, 12) == QColor("blue")
    assert rendered.pixelColor(190, 90) == QColor("red")
    moved = render_calls(surface, [replace(label, x=0.5)])
    assert moved.pixelColor(22, 12) == QColor("black")
    assert moved.pixelColor(102, 12) == QColor("blue")


def test_text_sits_on_its_bottom_anchor_by_its_ink_not_the_font_descent(surface: QuickSurface) -> None:
    rendered = render_calls(surface, [TextCall(0.1, 0.5, "xx", WHITE, anchor="bottom-left")])
    black = QColor("black")
    rows = [y for y in range(rendered.height()) if any(rendered.pixelColor(x, y) != black for x in range(20, 60))]
    # Lowercase letters without descenders end at the anchor, give or take a pixel of platform antialiasing; the
    # font's descent would leave them several pixels above it.
    assert rendered.height() // 2 - 1 - rows[-1] <= 1


def test_a_bar_without_background_or_padding_is_still_drawn(surface: QuickSurface) -> None:
    content = LabelContent((LabelRun("", (255, 255, 255), bar=1.0),), 0.12)
    rendered = render_calls(surface, [LabelCall(0.1, 0.5, "center-left", content)])
    black = QColor("black")
    assert any(rendered.pixelColor(x, y) != black for x in range(200) for y in range(100))


@pytest.mark.parametrize("origin", [(20, 10), (-20, -10)])
@pytest.mark.parametrize("opacity", [1.0, 0.5])
def test_new_and_returning_labels_keep_viewport_transform_and_opacity(
    surface: QuickSurface, origin: tuple[int, int], opacity: float
) -> None:
    """Changing label content after rendering preserves the video origin and overlay opacity."""
    content = LabelContent(
        (LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_x=0.06, padding_y=0.04
    )
    target = TARGET.translated(*origin)
    for text in ("A", "B", "A"):
        calls: DrawCalls = [
            BoxCall(0.4, 0.4, 0.2, 0.2, FILL),
            LabelCall(0.4, 0.4, "top-left", replace(content, runs=(LabelRun(text, (255, 255, 255)),))),
        ]
        surface.set_frame_image(solid_image(), target)
        surface.set_overlays(prepare_calls(calls, surface.drawing_buffer(target, 100)), opacity, target, 100, None)
        QApplication.processEvents()
        rendered = surface.grabFramebuffer()
        dpr = rendered.devicePixelRatio()
        color = rendered.pixelColor(round((82 + origin[0]) * dpr), round((42 + origin[1]) * dpr))
        assert abs(color.blue() - round(255 * opacity)) <= 1
        assert color.red() <= 1  # The label hides the box under it; the overlay fades as one.


def test_labels_survive_window_reparenting_and_graph_recreation(surface: QuickSurface, qtbot: QtBot) -> None:
    """Label layers belong to one node tree and are rebuilt for the new window's graphics resources."""
    label = LabelCall(
        0.1, 0.1, "top-left", LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255))
    )
    render_calls(surface, [label])
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(200, 100)
    surface.setParent(host)
    host.show()
    surface.show()
    assert render_calls(surface, [label]).pixelColor(21, 12) == QColor("blue")
    surface.cleanup()
    QApplication.processEvents()
    surface.show()
    surface.grabFramebuffer()
    assert render_calls(surface, [label]).pixelColor(21, 12) == QColor("blue")
    surface.setParent(None)
