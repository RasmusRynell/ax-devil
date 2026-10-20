"""Shape quads drawn through the overlay material: what the user sees of boxes, lines, polylines and polygons."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtGui import QImage

from ax_devil.modules.video_player.engine.drawing import Paint
from ax_devil.modules.video_player.engine.quick.shape_quads import (
    box_quads,
    circle_quads,
    line_quads,
    polygon_quads,
    polyline_quads,
)

from ..conftest import RenderQuads

SIZE = 100


def ink(image: QImage, x: int, y: int) -> int:
    """How much of a pixel the (red) pen or brush covers over the black ground, 0 to 255 plus or minus rounding."""
    value = int(image.pixelColor(x, y).red())
    return 255 if value > 240 else 0 if value < 24 else value


def arr(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float64)


def test_rounded_and_dashed_boxes_keep_the_stroke_centred_on_the_outline(render_quads: RenderQuads) -> None:
    paint = Paint(pen_width=0.04, radius=0.2, brush_a=0)
    image = render_quads(box_quads(arr(0.2), arr(0.2), arr(0.6), arr(0.6), paint, SIZE, SIZE, SIZE), SIZE)
    # Stroke is 4 px centred on x=20: ink from 18 to 22, none outside or at the centre of the box.
    assert ink(image, 20, 50) == 255 and ink(image, 17, 50) == 0 and ink(image, 24, 50) == 0
    assert ink(image, 50, 50) == 0
    # The corner is rounded: the square corner pixel is empty.
    assert ink(image, 19, 19) == 0

    dashed = Paint(pen_width=0.02, dashed=True)
    image = render_quads(box_quads(arr(0.1), arr(0.1), arr(0.8), arr(0.8), dashed, SIZE, SIZE, SIZE), SIZE)
    # Dash and gap are three stroke widths (6 px) each, from the top-left corner along the top edge.
    on = {x: ink(image, x, 10) > 0 for x in (12, 14, 18, 20, 24, 26)}
    assert on == {12: True, 14: True, 18: False, 20: False, 24: True, 26: True}


def test_dashed_circles_alternate_dashes_and_gaps_clockwise_from_the_top(render_quads: RenderQuads) -> None:
    dashed = Paint(pen_width=0.02, dashed=True, brush_a=0)
    image = render_quads(circle_quads(arr(0.5), arr(0.5), arr(0.4), dashed, SIZE, SIZE, SIZE), SIZE)

    # The centre line has radius 40 and 6 px dashes and gaps run along it: 3 px is mid-dash, 9 px mid-gap.
    def at(arc: float) -> int:
        angle = arc / 40
        return ink(image, round(50 + 40 * np.sin(angle)), round(50 - 40 * np.cos(angle)))

    assert at(3) > 0 and at(9) == 0 and at(15) > 0
    solid = render_quads(
        circle_quads(arr(0.5), arr(0.5), arr(0.4), Paint(pen_width=0.02, brush_a=0), SIZE, SIZE, SIZE), SIZE
    )
    assert ink(solid, round(50 + 40 * np.sin(0.225)), round(50 - 40 * np.cos(0.225))) > 0


def test_line_has_square_caps_and_the_stroke_width(render_quads: RenderQuads) -> None:
    paint = Paint(pen_width=0.1)
    image = render_quads(line_quads(arr(0.2), arr(0.5), arr(0.8), arr(0.5), paint, SIZE, SIZE, SIZE), SIZE)
    # A 10 px stroke from x=20 to x=80 extends 5 px past each end and 5 px to each side.
    assert ink(image, 16, 47) == 255 and ink(image, 83, 53) == 255
    assert ink(image, 13, 50) == 0 and ink(image, 87, 50) == 0 and ink(image, 50, 43) == 0
    # A line without stroke width is not drawn.
    assert not len(line_quads(arr(0.2), arr(0.5), arr(0.8), arr(0.5), Paint(pen_width=0), SIZE, SIZE, SIZE))


def test_polylines_have_round_joins_and_ends_and_dashes_that_run_along_them(render_quads: RenderQuads) -> None:
    corner = np.array([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8]])
    image = render_quads(polyline_quads([corner], Paint(pen_width=0.1), SIZE, SIZE, SIZE), SIZE)
    # A 10 px stroke: round ends reach 5 px past the start, but not into the corner a square end would cover.
    assert ink(image, 16, 20) == 255 and ink(image, 15, 15) == 0
    assert ink(image, 80, 50) == 255 and ink(image, 84, 16) == 0

    dashed = Paint(pen_width=0.02, dashed=True)
    bend = np.array([[0.1, 0.5], [0.5, 0.5], [0.5, 0.9]])
    image = render_quads(polyline_quads([bend], dashed, SIZE, SIZE, SIZE), SIZE)
    # The first segment is 40 px, so the pattern of 6 px dashes and gaps continues 4 px into the second one.
    assert [ink(image, 49, y) > 0 for y in (55, 61, 67, 73)] == [False, True, False, True]
    assert not len(polyline_quads([corner[:1]], dashed, SIZE, SIZE, SIZE))


def test_polygons_fill_any_orientation_without_seams_and_outline_the_closed_loop(render_quads: RenderQuads) -> None:
    fill = Paint(pen_width=0, brush_r=255, brush_a=255)
    triangle = np.array([[[0.2, 0.2], [0.8, 0.2], [0.2, 0.8]]])
    image = render_quads(polygon_quads(triangle, fill, SIZE, SIZE, SIZE, 1.0), SIZE)
    assert ink(image, 30, 30) == 255 and ink(image, 70, 70) == 0
    assert 80 < ink(image, 49, 50) < 180  # The hypotenuse is antialiased, not jagged or missing.
    # A notched polygon, as listed and reversed, is covered completely inside and not at all in the notch.
    notched = np.array([[[0.1, 0.1], [0.9, 0.1], [0.9, 0.3], [0.3, 0.3], [0.3, 0.9], [0.1, 0.9]]])
    forward = render_quads(polygon_quads(notched, fill, SIZE, SIZE, SIZE, 1.0), SIZE)
    backward = render_quads(polygon_quads(notched[:, ::-1], fill, SIZE, SIZE, SIZE, 1.0), SIZE)
    assert forward == backward
    assert all(ink(forward, x, y) == 255 for x in range(12, 88) for y in range(12, 28))
    assert all(ink(forward, x, y) == 255 for x in range(12, 28) for y in range(12, 88))
    assert ink(forward, 60, 60) == 0
    # Another corner on a proposed ear's diagonal must not let the ear cut across it.
    spiky = np.array([[(0, 0), (120, 0), (100, 40), (160, 60), (140, 60), (40, 140), (40, 60), (0, 60)]]) / 200
    assert ink(render_quads(polygon_quads(spiky, fill, 200, 200, 200, 1.0), 200), 30, 80) == 0
    # A corner on a straight edge is dropped from the triangles but the polygon still fills.
    straight = np.array([[(10, 10), (50, 10), (90, 10), (90, 30), (30, 30), (30, 90), (10, 90)]]) / 100
    assert ink(render_quads(polygon_quads(straight, fill, SIZE, SIZE, SIZE, 1.0), SIZE), 20, 20) == 255

    # A bow-tie fills its two lobes and nothing above or below the crossing, as the odd-even rule says.
    bowtie = np.array([[[0.2, 0.2], [0.8, 0.8], [0.8, 0.2], [0.2, 0.8]]])
    image = render_quads(polygon_quads(bowtie, fill, SIZE, SIZE, SIZE, 1.0), SIZE)
    assert ink(image, 30, 50) == 255 and ink(image, 70, 50) == 255
    assert ink(image, 50, 30) == 0 and ink(image, 50, 70) == 0

    square = np.array([[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]])
    image = render_quads(polygon_quads(square, Paint(pen_width=0.1), SIZE, SIZE, SIZE, 1.0), SIZE)
    assert ink(image, 20, 50) == 255 and ink(image, 50, 80) == 255 and ink(image, 50, 50) == 0
    assert ink(image, 84, 84) == 0  # Round joins.


def test_zero_area_and_non_finite_rows_produce_no_quads() -> None:
    paint = Paint(pen_width=0.01)
    nan = float("nan")
    boxes = box_quads(
        arr(0.1, 0.1, nan, 0.2),
        arr(0.1, 0.1, 0.1, 0.2),
        arr(0, 0.5, 0.5, 0.5),
        arr(0.5, 0, 0.5, 0.5),
        paint,
        SIZE,
        SIZE,
        SIZE,
    )
    assert len(boxes) == 4
    lines = line_quads(arr(0.1, nan), arr(0.1, 0.1), arr(0.5, 0.5), arr(0.5, 0.5), paint, SIZE, SIZE, SIZE)
    assert len(lines) == 4


def test_a_polygon_that_crosses_itself_too_often_is_drawn_as_its_outline_only() -> None:
    angle = np.arange(2001) * (np.pi * 1000 / 2001)  # A 2001-pointed star: about two million crossings.
    star = (0.5 + 0.4 * np.stack((np.cos(angle), np.sin(angle)), axis=1))[None]
    outline = Paint(pen_width=0.01)
    filled = polygon_quads(star, replace(outline, brush_r=255, brush_a=255), SIZE, SIZE, SIZE, 1.0)
    assert len(filled) == len(polygon_quads(star, outline, SIZE, SIZE, SIZE, 1.0))
