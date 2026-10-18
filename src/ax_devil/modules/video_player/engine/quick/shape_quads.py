"""Vectorised overlay quads for boxes, lines, circles, points, polylines and polygons, in target logical px.

Each function takes the arguments ``DrawingTarget`` receives (normalized coordinates and a ``Paint``) plus the target
size, and returns ``OVERLAY_VERTEX`` rows, four per drawn shape. Rows that would draw nothing (zero area, NaN, a
point with no stroke width) produce no quad. Callers cull by the culling rectangle first with ``Paint.take``.

Strokes are centred on the outline while the shader strokes inward, so outlined shapes grow by half the
stroke width and keep the same visible ink. Every shape quad is padded by one pixel for the shader's edge
antialiasing. Polylines and polygon outlines are one round-capped line quad per segment, so joins are round;
polygon fills are triangles of ``FILL`` quads with a one device pixel fringe that fades out to the edge.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from ..drawing import Paint
from .overlay_vertex import CORNER_U, CORNER_V, OVERLAY_VERTEX, PAD, OverlayKind, per_row, premultiplied

_DASH_PERIOD = 6.0
"""Dash period in stroke widths: a dash and a gap of three widths each."""
_MITER_LIMIT = 4.0
"""Longest a polygon fringe corner may reach, in fringe widths, before it is cut short."""


def _color(red: object, green: object, blue: object, alpha: object, count: int) -> NDArray[np.uint8]:
    """Premultiplied RGBA bytes, one row per shape."""
    rgb = np.column_stack([per_row(channel, count) for channel in (red, green, blue)])
    return premultiplied(rgb, per_row(alpha, count))


def _quads(
    kind: OverlayKind,
    origin: tuple[NDArray[np.float64], NDArray[np.float64]],
    axis: tuple[NDArray[np.float64], NDArray[np.float64]] | None,
    size: tuple[NDArray[np.float64], NDArray[np.float64]],
    radius: NDArray[np.float64],
    stroke: NDArray[np.float64],
    dash: NDArray[np.float64],
    fill: NDArray[np.uint8],
    pen: NDArray[np.uint8],
) -> NDArray[np.void]:
    """Pack shapes whose top-left corner is *origin* and whose u axis is the unit vector *axis* (None = +x)."""
    (ox, oy), (w, h) = origin, size
    ux, uy = (np.ones_like(w), np.zeros_like(w)) if axis is None else axis
    u = -PAD + (w[:, None] + 2 * PAD) * CORNER_U
    v = -PAD + (h[:, None] + 2 * PAD) * CORNER_V
    out = np.empty((len(w), 4), dtype=OVERLAY_VERTEX)
    out["x"] = ox[:, None] + ux[:, None] * u - uy[:, None] * v
    out["y"] = oy[:, None] + uy[:, None] * u + ux[:, None] * v
    out["u"], out["v"] = u, v
    out["w"], out["h"] = w[:, None], h[:, None]
    out["radius"], out["stroke"], out["dash"] = radius[:, None], stroke[:, None], dash[:, None]
    out["kind"] = float(kind)
    out["fill"], out["pen"] = fill[:, None], pen[:, None]
    return out.reshape(-1)


def _outlined(
    kind: OverlayKind,
    left: NDArray[np.float64],
    top: NDArray[np.float64],
    width: NDArray[np.float64],
    height: NDArray[np.float64],
    radius: NDArray[np.float64],
    paint: Paint,
    scale: float,
    keep: NDArray[np.bool_],
) -> NDArray[np.void]:
    """Quads for rectangles or ellipses outlined with the paint's pen, drawing only the *keep* rows."""
    count = len(left)
    stroke = np.maximum(0.0, per_row(paint.pen_width, count) * scale)
    keep = keep & np.isfinite(stroke)
    stroke = np.where(keep, stroke, 0.0)
    half = stroke / 2
    dashed = per_row(paint.dashed, count, np.bool_)
    dash = np.where(dashed & (kind == OverlayKind.ROUNDED_RECT), stroke * _DASH_PERIOD, 0.0)
    fill = _color(paint.brush_r, paint.brush_g, paint.brush_b, paint.brush_a, count)
    pen = _color(paint.pen_r, paint.pen_g, paint.pen_b, 255, count)
    rows = np.flatnonzero(keep)
    outer_w, outer_h = (width + stroke)[rows], (height + stroke)[rows]
    # Qt clamps the corner radius on the centre line; the outer edge is half a stroke further out.
    rounded = np.minimum(radius, np.minimum(width, height) / 2) + half
    rounded = np.where(radius > 0, rounded, 0.0)
    return _quads(
        kind,
        ((left - half)[rows], (top - half)[rows]),
        None,
        (outer_w, outer_h),
        rounded[rows],
        stroke[rows],
        dash[rows],
        fill[rows],
        pen[rows],
    )


def box_quads(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    w: NDArray[np.float64],
    h: NDArray[np.float64],
    paint: Paint,
    width: float,
    height: float,
    scale: float,
) -> NDArray[np.void]:
    """Rounded rectangles (radius 0 is square) for normalized boxes; ``Paint.radius`` and ``pen_width`` use *scale*."""
    left, top, box_w, box_h = x * width, y * height, w * width, h * height
    keep = (box_w > 0) & (box_h > 0) & np.isfinite(left) & np.isfinite(top) & np.isfinite(box_w) & np.isfinite(box_h)
    radius = np.nan_to_num(per_row(paint.radius, len(x)) * scale)
    return _outlined(OverlayKind.ROUNDED_RECT, left, top, box_w, box_h, radius, paint, scale, keep)


def circle_quads(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    radius: NDArray[np.float64],
    paint: Paint,
    width: float,
    height: float,
    scale: float,
) -> NDArray[np.void]:
    """Ellipse quads for circles whose radius is relative to the smaller target dimension."""
    r = radius * min(width, height)
    keep = (r > 0) & np.isfinite(r) & np.isfinite(x) & np.isfinite(y)
    return _outlined(
        OverlayKind.ELLIPSE, x * width - r, y * height - r, 2 * r, 2 * r, np.zeros(len(x)), paint, scale, keep
    )


def _squares(
    cx: NDArray[np.float64], cy: NDArray[np.float64], size: NDArray[np.float64], paint: Paint, keep: NDArray[np.bool_]
) -> NDArray[np.void]:
    """Solid squares of the pen colour centred on points."""
    keep = keep & (size > 0) & np.isfinite(size) & np.isfinite(cx) & np.isfinite(cy)
    colors = _color(paint.pen_r, paint.pen_g, paint.pen_b, 255, len(cx))[keep]
    size, zero = size[keep], np.zeros(np.count_nonzero(keep))
    return _quads(
        OverlayKind.ROUNDED_RECT,
        (cx[keep] - size / 2, cy[keep] - size / 2),
        None,
        (size, size),
        zero,
        zero,
        zero,
        colors,
        colors,
    )


def point_quads(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    paint: Paint,
    width: float,
    height: float,
    scale: float,
) -> NDArray[np.void]:
    """Square points as wide as the stroke width, centred on the point."""
    size = per_row(paint.pen_width, len(x)) * scale
    return _squares(x * width, y * height, size, paint, np.ones(len(x), dtype=np.bool_))


def line_quads(
    x1: NDArray[np.float64],
    y1: NDArray[np.float64],
    x2: NDArray[np.float64],
    y2: NDArray[np.float64],
    paint: Paint,
    width: float,
    height: float,
    scale: float,
) -> NDArray[np.void]:
    """Square-capped lines as rotated rectangles; a stroke of 0 or less draws nothing.

    Coincident endpoints draw a point as wide as the stroke.
    """
    count = len(x1)
    px1, py1, px2, py2 = x1 * width, y1 * height, x2 * width, y2 * height
    stroke = per_row(paint.pen_width, count) * scale
    finite = np.isfinite(px1) & np.isfinite(py1) & np.isfinite(px2) & np.isfinite(py2) & np.isfinite(stroke)
    dx, dy = px2 - px1, py2 - py1
    length = np.hypot(dx, dy)
    same = finite & (length == 0)
    rows = np.flatnonzero(finite & (length > 0) & (stroke > 0))
    points = _squares(px1, py1, stroke, paint, same)
    if not rows.size:
        return points
    length, stroke = length[rows], stroke[rows]
    ux, uy = dx[rows] / length, dy[rows] / length
    half = stroke / 2
    dashed = per_row(paint.dashed, count, np.bool_)[rows]
    pen = _color(paint.pen_r, paint.pen_g, paint.pen_b, 255, count)[rows]
    lines = _quads(
        OverlayKind.LINE,
        (px1[rows] - half * (ux - uy), py1[rows] - half * (uy + ux)),
        (ux, uy),
        (length + stroke, stroke),
        np.zeros(len(rows)),
        np.zeros(len(rows)),
        np.where(dashed, stroke * _DASH_PERIOD, 0.0),
        pen,
        pen,
    )
    return np.concatenate((lines, points))


def _segment_quads(
    start: NDArray[np.float64],
    end: NDArray[np.float64],
    row: NDArray[np.intp],
    rows: int,
    paint: Paint,
    scale: float,
) -> NDArray[np.void]:
    """Round-capped line quads for segments from *start* to *end* (pixels), in order, *row* being their paint row.

    The segments of a row follow each other, so a dash pattern continues across them. Zero-length segments, strokes
    of 0 or less and non-finite segments draw nothing.
    """
    delta = end - start
    finite = np.isfinite(start).all(axis=1) & np.isfinite(end).all(axis=1)
    length = np.where(finite, np.hypot(delta[:, 0], delta[:, 1]), 0.0)
    travelled = np.cumsum(length) - length
    phase = travelled - travelled[np.searchsorted(row, row)]
    stroke = per_row(paint.pen_width, rows)[row] * scale
    keep = np.flatnonzero((length > 0) & (stroke > 0) & np.isfinite(stroke))
    if not keep.size:
        return np.zeros(0, OVERLAY_VERTEX)
    row, length, stroke, phase = row[keep], length[keep], stroke[keep], phase[keep]
    ux, uy = delta[keep, 0] / length, delta[keep, 1] / length
    half = stroke / 2
    pen = _color(paint.pen_r, paint.pen_g, paint.pen_b, 255, rows)[row]
    return _quads(
        OverlayKind.LINE,
        (start[keep, 0] - half * (ux - uy), start[keep, 1] - half * (uy + ux)),
        (ux, uy),
        (length + stroke, stroke),
        half,
        phase - half,
        np.where(per_row(paint.dashed, rows, np.bool_)[row], stroke * _DASH_PERIOD, 0.0),
        pen,
        pen,
    )


def polyline_quads(
    points: Sequence[NDArray[np.float64]], paint: Paint, width: float, height: float, scale: float
) -> NDArray[np.void]:
    """Open polylines of normalized (k, 2) points: round joins and ends, dashes running along the whole line."""
    flat = np.concatenate(points) * (width, height) if points else np.zeros((0, 2))
    row = np.repeat(np.arange(len(points)), [len(shape) for shape in points])
    first = np.flatnonzero(row[:-1] == row[1:])
    return _segment_quads(flat[first], flat[first + 1], row[first], len(points), paint, scale)


def _turn(a: NDArray[np.float64], b: NDArray[np.float64], c: NDArray[np.float64]) -> NDArray[np.float64]:
    """Cross product of a to b and a to c: positive when c lies left of the way from a to b."""
    return (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0])


def _triangulate(points: NDArray[np.float64]) -> NDArray[np.intp]:
    """Triangles (n - 2, 3) of a positively oriented polygon by ear clipping; the rest is fanned if no ear is left.

    Corners that add no area are dropped, and their triangles are left as empty (0, 0, 0).

    A polygon that crosses itself has no ear at some point.
    """
    left = np.arange(len(points))
    triangles: list[tuple[int, int, int]] = []
    while len(left) > 3:
        count = len(left)
        here = points[left]
        before, after = np.roll(here, 1, axis=0), np.roll(here, -1, axis=0)
        corner = _turn(before, here, after)
        flat = np.flatnonzero(corner == 0)
        if flat.size:  # A straight or doubled-back corner adds no area.
            left = np.delete(left, flat[0])
            continue
        # blocked[i, j]: corner j lies in or on the triangle that clipping corner i would cut off, unless it is one of
        # that triangle's own corners (a polygon may touch itself there).
        blocked = (
            (_turn(before[:, None], here[:, None], here[None]) >= 0)
            & (_turn(here[:, None], after[:, None], here[None]) >= 0)
            & (_turn(after[:, None], before[:, None], here[None]) >= 0)
        )
        for own in (before, here, after):
            blocked &= ~np.all(own[:, None] == here[None], axis=2)
        ears = (corner > 0) & ~blocked.any(axis=1)
        if not ears.any():
            break
        ear = int(np.argmax(ears))
        triangles.append((left[ear - 1], left[ear], left[(ear + 1) % count]))
        left = np.delete(left, ear)
    triangles += [(left[0], left[index], left[index + 1]) for index in range(1, len(left) - 1)]
    triangles += [(0, 0, 0)] * (len(points) - 2 - len(triangles))  # Dropped corners leave empty triangles.
    return np.array(triangles, dtype=np.intp)


def _miter(points: NDArray[np.float64]) -> NDArray[np.float64]:
    """Offset direction of every corner of positively oriented polygons (rows, corners, 2), outward, miter limited."""
    edges = np.roll(points, -1, axis=1) - points
    lengths = np.maximum(np.hypot(edges[..., 0], edges[..., 1]), 1e-12)
    normals = np.stack((edges[..., 1], -edges[..., 0]), axis=-1) / lengths[..., None]
    direction = np.roll(normals, 1, axis=1) + normals
    direction /= np.maximum(np.hypot(direction[..., 0], direction[..., 1]), 1e-12)[..., None]
    scale = 1 / np.maximum(np.sum(direction * normals, axis=-1), 1 / _MITER_LIMIT)
    miter: NDArray[np.float64] = direction * scale[..., None]
    return miter


def _fill_quads(positions: NDArray[np.float64], colors: NDArray[np.uint8]) -> NDArray[np.void]:
    """``FILL`` quads from positions (k, 4, 2) and corner colours (k, 4, 4)."""
    out = np.zeros((len(positions), 4), OVERLAY_VERTEX)
    out["x"], out["y"] = positions[..., 0], positions[..., 1]
    out["kind"] = float(OverlayKind.FILL)
    out["fill"] = colors
    return out.reshape(-1)


def _fills(points: NDArray[np.float64], fill: NDArray[np.uint8], dpr: float) -> NDArray[np.void]:
    """Anti-aliased fills of polygons (rows, corners, 2) in pixels: triangles inside, a fading ring outside.

    The edge sits half a device pixel from where the colour is full and from where it is gone. Corners are mitered up
    to a limit. Convex polygons are fanned; others are ear clipped, and polygons that cross themselves approximate.
    """
    following = np.roll(points, -1, axis=1)
    area = np.sum(points[..., 0] * following[..., 1] - following[..., 0] * points[..., 1], axis=1) / 2
    ok = np.isfinite(area) & np.all(np.ptp(points, axis=1) > 0, axis=1)
    points, fill, area = points[ok], fill[ok], area[ok]
    if not len(points):
        return np.zeros(0, OVERLAY_VERTEX)
    points = np.where((area < 0)[:, None, None], points[:, ::-1], points)
    rows, corners = points.shape[:2]
    edges = np.roll(points, -1, axis=1) - points
    ahead = np.roll(edges, -1, axis=1)
    convex = np.all(edges[..., 0] * ahead[..., 1] - edges[..., 1] * ahead[..., 0] >= 0, axis=1)
    fringe = 0.5 / dpr
    miter = _miter(points)
    # Polygons thinner than the fringe band keep their outline as the inside edge instead of inverting.
    thin = (4 * np.abs(area) / np.sum(np.hypot(edges[..., 0], edges[..., 1]), axis=1) < 4 * fringe)[:, None, None]
    inner = np.where(thin, points, points - miter * fringe)
    outer = points + miter * fringe
    triangles = np.tile(np.array([(0, index, index + 1) for index in range(1, corners - 1)]), (rows, 1, 1))
    for row in np.flatnonzero(~convex):
        triangles[row] = _triangulate(inner[row])
    gathered = inner[np.arange(rows)[:, None, None], triangles]  # (rows, triangles, 3, 2)
    inside = gathered[:, :, [0, 1, 2, 2]].reshape(-1, 4, 2)  # A triangle repeats its last corner.
    ring = np.stack((outer, np.roll(outer, -1, axis=1), inner, np.roll(inner, -1, axis=1)), axis=2).reshape(-1, 4, 2)
    faded = np.zeros((rows, corners, 4, 4), np.uint8)
    faded[:, :, 2:] = fill[:, None, None, :]
    return np.concatenate(
        (
            _fill_quads(inside, np.repeat(fill, corners - 2, axis=0)[:, None, :]),
            _fill_quads(ring, faded.reshape(-1, 4, 4)),
        )
    )


def polygon_quads(
    points: NDArray[np.float64], paint: Paint, width: float, height: float, scale: float, dpr: float
) -> NDArray[np.void]:
    """Closed polygons of normalized points (rows, corners, 2): a fill where the brush shows, then the outline."""
    rows, corners = points.shape[:2]
    pixels = points * (width, height)
    filled = np.flatnonzero(per_row(paint.brush_a, rows) > 0)
    fills = _fills(
        pixels[filled], _color(paint.brush_r, paint.brush_g, paint.brush_b, paint.brush_a, rows)[filled], dpr
    )
    row = np.repeat(np.arange(rows), corners)
    outline = _segment_quads(pixels.reshape(-1, 2), np.roll(pixels, -1, axis=1).reshape(-1, 2), row, rows, paint, scale)
    return np.concatenate((fills, outline))
