"""Retained numeric rectangles and convex polygons with premultiplied coverage fringes for hardware Quick."""

from __future__ import annotations

import ctypes
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtQuick import QQuickItem, QSGGeometry, QSGGeometryNode, QSGMaterial, QSGNode, QSGVertexColorMaterial

# QSGGeometry retains the attribute pointer: this wrapper must outlive every node.
_ATTRIBUTES = QSGGeometry.defaultAttributes_ColoredPoint2D()
_VERTEX = struct.Struct("=ffBBBB")
VERTEX = np.dtype([("x", "=f4"), ("y", "=f4"), ("color", "u1", (4,))])
_Point = tuple[float, float]
# A vertex takes the fill or the pen color, scaled by its coverage weight (0 is transparent).
_Shade = tuple[bool, float]
_CLEAR: _Shade = (False, 0.0)
_FILL: _Shade = (False, 1.0)


def premultiplied(red: int, green: int, blue: int, alpha: int = 255) -> tuple[int, int, int, int]:
    """Return the premultiplied byte channels required by QSGVertexColorMaterial."""
    return round(red * alpha / 255), round(green * alpha / 255), round(blue * alpha / 255), alpha


def _rectangle(width: float, height: float, inset: float) -> tuple[_Point, ...]:
    return ((inset, inset), (width - inset, inset), (width - inset, height - inset), (inset, height - inset))


def _bevel(width: float, height: float, distance: float) -> tuple[_Point, ...]:
    if distance >= 0:
        return (
            (-distance, 0),
            (0, -distance),
            (width, -distance),
            (width + distance, 0),
            (width + distance, height),
            (width, height + distance),
            (0, height + distance),
            (-distance, height),
        )
    # Duplicate interior corners to match the eight vertices on the beveled outside.
    return tuple(point for point in _rectangle(width, height, -distance) for _ in range(2))


def _ring(
    parts: list[tuple[_Point, _Shade]],
    outer: tuple[_Point, ...],
    inner: tuple[_Point, ...],
    outside: _Shade,
    inside: _Shade,
) -> None:
    for index in range(len(outer)):
        following = (index + 1) % len(outer)
        parts.extend(
            (
                (outer[index], outside),
                (outer[following], outside),
                (inner[following], inside),
                (outer[index], outside),
                (inner[following], inside),
                (inner[index], inside),
            )
        )


@dataclass(frozen=True, slots=True)
class Shading:
    """Per-vertex color class: which of a few (pen role, weight) pairs colors each vertex."""

    classes: NDArray[np.intp]
    keys: tuple[tuple[bool, float], ...]

    @classmethod
    def of(cls, pen: NDArray[np.bool_], weight: NDArray[np.float64]) -> Shading:
        """Group vertices by their pen role and weight."""
        pairs = list(zip(pen.tolist(), weight.tolist()))
        keys = tuple(dict.fromkeys(pairs))
        index = {key: position for position, key in enumerate(keys)}
        return cls(np.array([index[pair] for pair in pairs], dtype=np.intp), keys)

    def colors(self, fill: NDArray[np.float64], pen: NDArray[np.float64]) -> NDArray[np.uint8]:
        """Premultiplied byte colors per vertex, for (rows, 4) or shared (4,) fill and pen colors."""
        fill, pen = np.broadcast_arrays(fill, pen)
        palette = np.stack([np.rint((pen if role else fill) * weight) for role, weight in self.keys], axis=-2)
        return palette.astype(np.uint8)[..., self.classes, :]


@dataclass(frozen=True, slots=True)
class RectangleTemplate:
    """Color-free rectangle topology: positions and color class per vertex."""

    xy: NDArray[np.float32]
    shading: Shading
    size: float

    @property
    def vertex_count(self) -> int:
        """Return the number of vertices per rectangle."""
        return len(self.xy)


@lru_cache(maxsize=256)
def rectangle_template(stroke: float, fill: bool, dpr: float) -> RectangleTemplate:
    """Build topology once per stroke width; bulk array operations apply sizes, transforms and colors."""
    # Frame-to-frame size changes only translate the right/bottom vertices; no Python vertex loop is needed again.
    fringe = 0.5 / dpr
    size = max(2.0, stroke + 2 * fringe + 1)
    parts: list[tuple[_Point, _Shade]] = []
    if fill:
        inner = _rectangle(size, size, fringe)
        parts.extend((inner[index], _FILL) for index in (0, 1, 2, 0, 2, 3))
        _ring(parts, _rectangle(size, size, -fringe), inner, _CLEAR, _FILL)
    if stroke > 0:
        half = stroke / 2
        peak = max(0.0, half - fringe)
        pen: _Shade = (True, min(1.0, stroke / (2 * fringe)))
        outer, first, second, inside = (
            _bevel(size, size, offset) for offset in (half + fringe, peak, -peak, -half - fringe)
        )
        _ring(parts, outer, first, _CLEAR, pen)
        if peak > 0:
            _ring(parts, first, second, pen, pen)
        _ring(parts, second, inside, pen, _CLEAR)
    return RectangleTemplate(
        np.asarray([point for point, _ in parts], dtype=np.float32).reshape(-1, 2),
        Shading.of(
            np.asarray([paint[0] for _, paint in parts], dtype=np.bool_),
            np.asarray([paint[1] for _, paint in parts], dtype=np.float64),
        ),
        size,
    )


_MITER_LIMIT = 4.0


def _widths(points: NDArray[np.float64]) -> NDArray[np.float64]:
    """Polygon width as 4 * area / perimeter, which equals the shorter side of a rectangle."""
    edges = np.roll(points, -1, axis=1) - points
    following = np.roll(points, -1, axis=1)
    area = np.abs(np.sum(points[..., 0] * following[..., 1] - following[..., 0] * points[..., 1], axis=1)) / 2
    with np.errstate(all="ignore"):
        return cast(NDArray[np.float64], 4 * area / np.sum(np.hypot(edges[..., 0], edges[..., 1]), axis=1))


def convex_rows(points: NDArray[np.float64], minimum: NDArray[np.float64]) -> NDArray[np.bool_]:
    """Rows of (rows, corners, 2) polygons that are finite, strictly convex and at least ``minimum`` wide."""
    edges = np.roll(points, -1, axis=1) - points
    following = np.roll(edges, -1, axis=1)
    turns = edges[..., 0] * following[..., 1] - edges[..., 1] * following[..., 0]
    lengths = np.hypot(edges[..., 0], edges[..., 1])
    convex = np.all(turns > 0, axis=1) | np.all(turns < 0, axis=1)
    finite = np.all(np.isfinite(points), axis=(1, 2))
    long = np.all(lengths >= minimum[:, None], axis=1)
    return convex & finite & long & (_widths(points) >= minimum)


@dataclass(frozen=True, slots=True)
class _Corners:
    """Edge normals of positively oriented convex polygons, shared by every offset ring."""

    points: NDArray[np.float64]
    before: NDArray[np.float64]
    after: NDArray[np.float64]
    miter: NDArray[np.float64]

    @classmethod
    def of(cls, points: NDArray[np.float64]) -> _Corners:
        edges = np.roll(points, -1, axis=1) - points
        normals = np.stack((edges[..., 1], -edges[..., 0]), axis=-1) / np.hypot(edges[..., 0], edges[..., 1])[..., None]
        before = np.roll(normals, 1, axis=1)
        direction = before + normals
        direction /= np.maximum(np.hypot(direction[..., 0], direction[..., 1]), 1e-12)[..., None]
        scale = 1 / np.maximum(np.sum(direction * normals, axis=-1), 1 / _MITER_LIMIT)
        return cls(points, before, normals, direction * scale[..., None])

    def offset(self, distance: NDArray[np.float64]) -> NDArray[np.float64]:
        """Offset every corner by a per-row distance, two points per corner.

        Outward offsets bevel each corner between its edge normals, as Qt's path strokes do; inward
        offsets meet at the clamped miter point, repeated so both rings of a band have equal length.
        """
        rows, corners = self.points.shape[:2]
        shift = distance[:, None, None]
        if np.all(distance >= 0):
            pair = np.stack((self.points + self.before * shift, self.points + self.after * shift), axis=2)
            return cast(NDArray[np.float64], pair.reshape(rows, 2 * corners, 2))
        return cast(NDArray[np.float64], np.repeat(self.points + self.miter * shift, 2, axis=1))


@dataclass(frozen=True, slots=True)
class PolygonBatch:
    """Convex polygons with the same corner count: anti-aliased fill and stroke rings in final pixels.

    Each vertex takes the fill or the pen color scaled by its weight; pen colors already include
    each row's stroke coverage. Colors are premultiplied (rows, 4) arrays or one shared color.
    """

    x: NDArray[np.float32]
    y: NDArray[np.float32]
    shading: Shading
    fill: NDArray[np.float64]
    pen: NDArray[np.float64]

    @property
    def rows(self) -> int:
        """Return the number of polygons."""
        return len(self.x)

    @property
    def size(self) -> int:
        """Return the number of vertices per polygon."""
        return int(self.x.shape[1])

    def write(self, target: NDArray[np.void]) -> None:
        """Copy the final positions and color each vertex."""
        target["x"], target["y"] = self.x, self.y
        target["color"] = self.shading.colors(self.fill, self.pen)


def polygon_batch(
    points: NDArray[np.float64],
    stroke: NDArray[np.float64],
    fill: NDArray[np.float64],
    pen: NDArray[np.float64],
    filled: bool,
    dpr: float,
) -> PolygonBatch:
    """Build vertices for convex polygons; colors are premultiplied (rows, 4) arrays or one shared color."""
    following = np.roll(points, -1, axis=1)
    area = np.sum(points[..., 0] * following[..., 1] - following[..., 0] * points[..., 1], axis=1)
    points = np.where((area < 0)[:, None, None], points[:, ::-1], points)
    rows, corners = points.shape[:2]
    corners_of = _Corners.of(points)
    fringe = np.full(rows, 0.5 / dpr)
    xs: list[NDArray[np.float64]] = []
    ys: list[NDArray[np.float64]] = []
    layout: list[tuple[int, tuple[_Shade, ...]]] = []

    def ring(outer: NDArray[np.float64], inner: NDArray[np.float64], outside: _Shade, inside: _Shade) -> None:
        # Two triangles per edge, edge by edge: outer i, outer i+1, inner i+1 and outer i, inner i+1, inner i.
        count = outer.shape[1]
        current = np.arange(count)
        following = (current + 1) % count
        corner = np.stack((current, following, following, current, following, current), axis=1).reshape(-1)
        side = np.tile(_RING_SIDES, count)
        picks = np.stack((outer, inner), axis=2)[:, corner, side]
        xs.append(picks[..., 0])
        ys.append(picks[..., 1])
        layout.append((count, tuple(inside if inner_side else outside for inner_side in _RING_SIDES)))

    pen_colors = np.zeros(4)
    if filled:
        # Polygons thinner than the fringe band keep their outline as the inside edge instead of inverting.
        thin = (_widths(points) < 4 * fringe)[:, None, None]
        inside = np.where(thin, np.repeat(points, 2, axis=1), corners_of.offset(-fringe))
        fan = 2 * np.array([(0, index, index + 1) for index in range(1, corners - 1)]).reshape(-1)
        xs.append(inside[:, fan, 0])
        ys.append(inside[:, fan, 1])
        layout.append((len(fan), (_FILL,)))
        ring(corners_of.offset(fringe), inside, _CLEAR, _FILL)
    if np.any(stroke > 0):
        half = stroke / 2
        peak = np.maximum(0.0, half - fringe)
        coverage = np.minimum(1.0, stroke / (2 * fringe))
        pen_colors = (np.broadcast_to(pen, (rows, 4)) if pen.ndim == 1 else pen) * coverage[:, None]
        outer, first, second, inner = (
            corners_of.offset(offset) for offset in (half + fringe, peak, -peak, -half - fringe)
        )
        stroked: _Shade = (True, 1.0)
        ring(outer, first, _CLEAR, stroked)
        if np.any(peak > 0):
            ring(first, second, stroked, stroked)
        ring(second, inner, stroked, _CLEAR)
    return PolygonBatch(
        np.concatenate(xs, axis=1).astype(np.float32),
        np.concatenate(ys, axis=1).astype(np.float32),
        _shades(tuple(layout)),
        fill,
        pen_colors,
    )


_RING_SIDES = np.array([0, 0, 1, 0, 1, 1])


@lru_cache(maxsize=64)
def _shades(layout: tuple[tuple[int, tuple[_Shade, ...]], ...]) -> Shading:
    """Per-vertex color classes: each part repeats its shade pattern over its edges or triangles."""
    shades = [shade for count, pattern in layout for _ in range(count) for shade in pattern]
    return Shading.of(
        np.array([role for role, _ in shades], dtype=np.bool_), np.array([weight for _, weight in shades])
    )


class GeometryItem(QQuickItem):
    """Own a local colored mesh; Qt handles position, rotation, opacity and ordering."""

    def __init__(self, parent: QQuickItem) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents)
        self.setTransformOrigin(QQuickItem.TransformOrigin.TopLeft)
        self._payload = b""

    def set_mesh(self, payload: bytes) -> None:
        """Queue changed geometry without dirtying the node for movement alone."""
        self.setVisible(bool(payload))
        if payload != self._payload:
            self._payload = payload
            self.update()

    def updatePaintNode(  # noqa: N802
        self, old_node: QSGNode | None, data: QQuickItem.UpdatePaintNodeData
    ) -> QSGNode:
        """Copy prepared vertices into Qt-owned storage during scene graph synchronization."""
        node = cast(QSGGeometryNode, old_node) if old_node is not None else QSGGeometryNode()
        count, remainder = divmod(len(self._payload), _VERTEX.size)
        assert remainder == 0
        if old_node is None:
            geometry = QSGGeometry(_ATTRIBUTES, count)
            geometry.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangles)
            geometry.setVertexDataPattern(QSGGeometry.DataPattern.DynamicPattern)
            node.setGeometry(geometry)
            node.setFlag(QSGNode.Flag.OwnsGeometry)
            material = QSGVertexColorMaterial()
            material.setFlag(QSGMaterial.Flag.Blending)
            node.setMaterial(material)
            node.setFlag(QSGNode.Flag.OwnsMaterial)
        geometry = node.geometry()
        if geometry.vertexCount() != count:
            geometry.allocate(count)
        assert geometry.sizeOfVertex() == _VERTEX.size
        if self._payload:
            # PySide exposes the allocated native buffer as a pointer, not a writable
            # Python buffer. Copy exactly the allocated vertex count, on Qt's thread.
            ctypes.memmove(int(geometry.vertexData()), self._payload, len(self._payload))
        geometry.markVertexDataDirty()
        node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
        return node
