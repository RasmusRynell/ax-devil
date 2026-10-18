"""Rendered geometry, paths and text of the native Quick backend."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtQuick import QSGRendererInterface

from ax_devil.modules.video_player.engine.drawing import (
    DrawingStyle,
    Paint,
    Points,
    _rectangle_path,
)
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from tests.drawing_helpers import (
    BoxCall,
    CircleCall,
    DrawCall,
    DrawCalls,
    LineCall,
    Numbers,
    PointCall,
    PolygonCall,
    PolylineCall,
    TextCall,
)
from tests.helpers.quick_surface import (
    FILL,
    WHITE,
    CompoundCalls,
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
        (PolygonCall(((0.2, 0.2), (0.8, 0.8), (0.2, 0.8), (0.8, 0.2)), FILL), (100, 30), (50, 50)),
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


def test_order_opacity_and_removal_survive_reused_path_slots(surface: QuickSurface) -> None:
    blue = DrawingStyle(pen_width=0, brush_b=255, brush_a=255)
    primitives: DrawCalls = [BoxCall(0.1, 0.1, 0.6, 0.6, FILL), BoxCall(0.3, 0.3, 0.6, 0.6, blue)]
    actual = render_calls(surface, primitives, 0.5)
    for point, expected in (
        ((40, 20), QColor(128, 0, 0)),
        ((100, 50), QColor(64, 0, 128)),
        ((170, 80), QColor(0, 0, 128)),
    ):
        color = actual.pixelColor(*point)
        assert abs(color.red() - expected.red()) <= 2
        assert abs(color.blue() - expected.blue()) <= 2
    actual = render_calls(surface, [primitives[0]])
    assert actual.pixelColor(100, 50) == QColor("red")
    assert actual.pixelColor(170, 80) == QColor("black")
    assert render_calls(surface, []).pixelColor(40, 20) == QColor("black")


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


def test_replacing_values_in_producer_list_updates_geometry(surface: QuickSurface) -> None:
    box = BoxCall(0.1, 0.1, 0.2, 0.2, FILL)
    primitives: DrawCalls = [box]
    assert render_calls(surface, primitives).pixelColor(30, 20) == QColor("red")
    primitives[0] = replace(box, x=0.6)
    moved = render_calls(surface, primitives)
    assert moved.pixelColor(30, 20) == QColor("black")
    assert moved.pixelColor(130, 20) == QColor("red")
    polygon = PolygonCall(((0.1, 0.1), (0.3, 0.1), (0.3, 0.3)), FILL)
    primitives[:] = [polygon]
    assert render_calls(surface, primitives).pixelColor(50, 15) == QColor("red")
    primitives[0] = replace(polygon, points=((0.6, 0.1), (0.8, 0.1), (0.8, 0.3)))
    moved = render_calls(surface, primitives)
    assert moved.pixelColor(50, 15) == QColor("black")
    assert moved.pixelColor(150, 15) == QColor("red")


def test_independent_path_movement_and_style_changes(surface: QuickSurface) -> None:
    """Moving a neighboring shape must not alter retained geometry or paint order."""
    red = BoxCall(0.1, 0.1, 0.3, 0.5, FILL)
    blue_style = replace(FILL, brush_r=0, brush_b=255)
    blue = BoxCall(0.5, 0.1, 0.3, 0.5, blue_style)
    render_calls(surface, [red, blue])
    moved = render_calls(surface, [red, replace(blue, x=0.2)])
    assert moved.pixelColor(30, 30) == QColor("red")
    assert moved.pixelColor(50, 30) == QColor("blue")
    assert moved.pixelColor(140, 30) == QColor("black")
    changed = render_calls(surface, [red, replace(blue, w=0.1, style=FILL)])
    assert changed.pixelColor(110, 30) == QColor("red")
    assert changed.pixelColor(140, 30) == QColor("black")


def test_zero_output_instruction_changes_slot_alignment(surface: QuickSurface) -> None:
    """An invisible instruction becoming visible must shift subsequent retained slots."""
    invisible = BoxCall(0, 0, 0, 0.5, FILL)
    blue = BoxCall(0.5, 0, 0.5, 1, replace(FILL, brush_r=0, brush_b=255))
    render_calls(surface, [invisible, blue])
    visible = render_calls(surface, [replace(invisible, w=0.5), blue])
    assert visible.pixelColor(50, 25) == QColor("red")
    assert visible.pixelColor(150, 25) == QColor("blue")
    hidden = render_calls(surface, [invisible, blue])
    assert hidden.pixelColor(50, 25) == QColor("black")
    assert hidden.pixelColor(150, 25) == QColor("blue")


def test_unchanged_compound_calls_survive_neighbor_updates(surface: QuickSurface) -> None:
    """Partial updates preserve groups of interleaved drawing calls."""
    box = BoxCall(0, 0, 0.4, 1, FILL)
    compound = CompoundCalls(
        (
            BoxCall(0.5, 0, 0.5, 1, replace(FILL, brush_r=0, brush_b=255)),
            TextCall(0.55, 0.1, "Text", WHITE, anchor="top-left"),
        )
    )
    original = render_calls(surface, [box, compound])
    for replacement in (replace(box, w=0.2), replace(box, w=0), box):
        updated = render_calls(surface, [replacement, compound])
        assert updated.copy(100, 0, 100, 100) == original.copy(100, 0, 100, 100)


def _reference_boxes(canvas: DrawingBuffer, x: Numbers, y: Numbers, w: Numbers, h: Numbers, paint: Paint) -> None:
    s = canvas.settings
    for row in range(len(x)):
        canvas.path(
            _rectangle_path(float(w[row]) * s.width, float(h[row]) * s.height),
            paint.style(row),
            fill=True,
            origin=QPointF(x[row] * s.width, y[row] * s.height),
        )


def _reference_lines(canvas: DrawingBuffer, x1: Numbers, y1: Numbers, x2: Numbers, y2: Numbers, paint: Paint) -> None:
    s = canvas.settings
    for row in range(len(x1)):
        dx, dy = (x2[row] - x1[row]) * s.width, (y2[row] - y1[row]) * s.height
        canvas.path(
            f"m 0 0 l {dx:.12g} {dy:.12g}",
            paint.style(row),
            fill=False,
            origin=QPointF(x1[row] * s.width, y1[row] * s.height),
        )


def _reference_polygons(canvas: DrawingBuffer, points: Sequence[Points], paint: Paint) -> None:
    for row, shape in enumerate(points):
        canvas._polygon(shape, paint.style(row), True)


@pytest.mark.parametrize("stroke", [0.005, 0.02, 0.06])
@pytest.mark.parametrize("alpha", [15, 255])
def test_numeric_geometry_preserves_path_coverage(
    surface: QuickSurface, monkeypatch: pytest.MonkeyPatch, stroke: float, alpha: int
) -> None:
    """Compare fractional, translucent, beveled and reversed geometry with Qt paths, one primitive at a time.

    Edge coverage is approximate, but interiors, bounds and opacity must agree. Order within the
    geometry layer is not preserved, so overlapping primitives are not compared together.
    Run this test with native OpenGL and QT_SCALE_FACTOR=1, 1.5 and 2 as well as software.
    """
    style = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=stroke, brush_g=180, brush_a=alpha)
    primitives: DrawCalls = [
        BoxCall(0.10125, 0.1025, 0.35, 0.35, style),
        BoxCall(0.35, 0.2, 0.2, 0.6, replace(style, pen_width=0)),
        LineCall(0.9, 0.9, 0.6, 0.2, style),
        LineCall(0.05, 0.6, 0.4, 0.6, style),
        LineCall(0.55, 0.1, 0.55, 0.4, style),
        PolygonCall(((0.62, 0.62), (0.95, 0.66), (0.9, 0.95), (0.66, 0.9)), style),
        PolygonCall(((0.1, 0.95), (0.2, 0.7), (0.3, 0.95)), replace(style, pen_width=0)),
    ]
    render_calls(surface, primitives, 0.6)
    hardware = surface.quickWindow().rendererInterface().graphicsApi() != QSGRendererInterface.GraphicsApi.Software
    assert bool(surface._overlays._geometry) == hardware
    for primitive in primitives:
        render_calls(surface, [])
        actual = render_calls(surface, [primitive], 0.6).convertToFormat(QImage.Format.Format_RGBA8888)
        render_calls(surface, [])
        with monkeypatch.context() as patch:
            patch.setattr(DrawingBuffer, "boxes", _reference_boxes)
            patch.setattr(DrawingBuffer, "lines", _reference_lines)
            patch.setattr(DrawingBuffer, "polygons", _reference_polygons)
            expected = render_calls(surface, [primitive], 0.6).convertToFormat(QImage.Format.Format_RGBA8888)
        shape = (actual.height(), actual.width(), 4)
        actual_pixels = np.frombuffer(actual.constBits(), dtype=np.uint8).reshape(shape).astype(np.int16)
        expected_pixels = np.frombuffer(expected.constBits(), dtype=np.uint8).reshape(shape).astype(np.int16)
        difference = np.abs(actual_pixels - expected_pixels)
        # At fractional DPR Qt's square-cap endpoint can be a half-coverage sample
        # brighter than the numeric fringe. Keep differences confined to boundaries.
        assert difference.max() <= 80, f"{primitive}"
        assert difference.mean() < 0.8
        uniform = np.ones(shape[:2], dtype=np.bool_)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                neighbor = np.roll(expected_pixels, (dy, dx), axis=(0, 1))
                uniform &= np.max(np.abs(expected_pixels - neighbor), axis=2) <= 2
        assert difference[uniform].max() <= 2


def test_geometry_and_path_transitions_preserve_order_and_pool_alignment(surface: QuickSurface) -> None:
    """Changing eligibility must not reuse a neighboring operation's retained contents."""
    first = BoxCall(0.1, 0.1, 0.6, 0.6, FILL)
    compound = CompoundCalls(
        (
            CircleCall(0.6, 0.5, 0.2, replace(FILL, brush_r=0, brush_b=255)),
            BoxCall(0.6, 0.1, 0.3, 0.4, replace(FILL, brush_r=0, brush_g=255)),
            TextCall(0.6, 0.1, "Text", WHITE, anchor="top-left"),
        )
    )
    original = render_calls(surface, [first, compound])
    for replacement in (
        replace(first, style=replace(FILL, pen_style="dash")),
        replace(first, w=0.002),
        LineCall(0.1, 0.1, 0.3, 0.3, WHITE),
        first,
    ):
        updated = render_calls(surface, [replacement, compound])
        assert updated.copy(150, 0, 50, 100) == original.copy(150, 0, 50, 100)
    assert render_calls(surface, [first, compound]) == original
