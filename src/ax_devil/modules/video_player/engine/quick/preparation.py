"""Final geometry, path and glyph preparation from drawing batches, for display and export."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import NamedTuple, cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor

from ..drawing import (
    _ANCHORS,
    DrawingStyle,
    LabelContent,
    LabelSprite,
    Paint,
    Points,
    _circle_path,
    _poly_bounds,
    _poly_path,
    _rectangle_path,
    _rounded_rectangle_path,
    _text_block,
    _TextBlock,
    label_sprite,
    text_ink_bounds,
)
from ..render_context import RenderContext
from .batching import GeometryPacker, RectangleBatch, VertexBatch
from .geometry import convex_rows, polygon_batch, premultiplied, rectangle_template


@lru_cache(maxsize=1024)
def color(red: int, green: int, blue: int, alpha: int = 255) -> QColor:
    """Share immutable colors across prepared frames."""
    return QColor(red, green, blue, alpha)


@dataclass(frozen=True, slots=True)
class DrawingSettings:
    """Exact display/export geometry and backend inputs, separate from catalog units."""

    width: float
    height: float
    scale: float
    dpi: int = 96
    dpr: float = 1.0
    hardware: bool = False
    viewport: tuple[float, float, float, float] | None = None

    @classmethod
    def for_context(cls, context: RenderContext) -> DrawingSettings:
        """Construct uncropped software preparation settings for a logical target."""
        return cls(context.width, context.height, context.scale_factor)


class MeshData(NamedTuple):
    """Final screen-space vertex bytes for one geometry node."""

    payload: bytes
    operation_count: int = 1


class PathData(NamedTuple):
    """Resolved Qt Shape properties for geometry delegated to Qt's path renderer."""

    geometry: str
    position: QPointF
    stroke: QColor
    fill: QColor
    width: float
    dashed: bool


class LabelData(NamedTuple):
    """A painted label and its native top-left position, snapped to device pixels."""

    sprite: LabelSprite
    position: QPointF


class TextData(NamedTuple):
    """Shaped glyph layouts, color and native position; no text interpretation remains."""

    block: _TextBlock
    position: QPointF
    color: QColor


@dataclass(frozen=True, slots=True)
class PreparedDrawing:
    """Immutable submission data; no normalized drawing objects or replay program.

    Kinds are layers drawn bottom to top as meshes, paths, texts, then labels, so text stays readable.
    Order across entities is not preserved; within the mesh layer emission order is kept.
    """

    meshes: tuple[MeshData, ...] = ()
    paths: tuple[PathData, ...] = ()
    texts: tuple[TextData, ...] = ()
    labels: tuple[LabelData, ...] = ()
    counts: tuple[tuple[str, int], ...] = ()

    def __len__(self) -> int:
        operations = sum(mesh.operation_count for mesh in self.meshes)
        return operations + len(self.paths) + len(self.texts) + len(self.labels)


EMPTY_DRAWING = PreparedDrawing()


def _floats(value: object, count: int) -> NDArray[np.float64]:
    """Broadcast one shared value or per-row values to one float per row."""
    return np.broadcast_to(np.asarray(value, dtype=np.float64), (count,))


def _colors(red: object, green: object, blue: object, alpha: object, rows: NDArray[np.intp]) -> NDArray[np.float64]:
    """Premultiplied colors for the selected rows, or one color shared by every row."""
    channels = [np.asarray(channel, dtype=np.float64) for channel in (red, green, blue, alpha)]
    if all(channel.ndim == 0 for channel in channels):
        return np.asarray(premultiplied(*(int(channel) for channel in channels)), dtype=np.float64)
    r, g, b, a = (channel[rows] if channel.ndim else np.full(len(rows), channel) for channel in channels)
    return np.stack((np.rint(r * a / 255), np.rint(g * a / 255), np.rint(b * a / 255), a), axis=1)


def _join(values: list[object]) -> object:
    """Concatenate per-row arguments of several batches."""
    if all(type(value) is np.ndarray for value in values):
        return np.concatenate(cast(list[NDArray[np.generic]], values))
    return [item for value in values for item in cast(Sequence[object], value)]


def _select(colors: NDArray[np.float64], rows: NDArray[np.intp]) -> NDArray[np.float64]:
    return colors[rows] if colors.ndim == 2 else colors


class DrawingBuffer:
    """Prepare final backend data from batches of compiled catalog instructions."""

    def __init__(self, settings: DrawingSettings) -> None:
        self.settings = settings
        self._viewport = settings.viewport
        self._meshes: list[VertexBatch] = []
        self._paths: list[PathData] = []
        self._texts: list[TextData] = []
        self._labels: list[LabelData] = []
        self._counts: Counter[str] = Counter()
        self._queued: defaultdict[tuple[str, int], list[tuple[tuple[object, ...], Paint]]] = defaultdict(list)
        self._previous = EMPTY_DRAWING
        self._packer = GeometryPacker()

    def reset(self, settings: DrawingSettings) -> None:
        """Start a frame; live glyphs and shared topology retain their own resources."""
        self.settings = settings
        self._viewport = settings.viewport
        self._meshes, self._paths, self._texts, self._labels, self._counts = [], [], [], [], Counter()
        self._queued = defaultdict(list)

    def finish(self) -> PreparedDrawing:
        """Prepare every queued batch, then publish without retaining mutable builder collections."""
        self._flush()
        drawing = PreparedDrawing(
            tuple(MeshData(payload, count) for payload, count in self._packer.pack(self._meshes)),
            tuple(self._paths),
            tuple(self._texts),
            tuple(self._labels),
            tuple(sorted(self._counts.items())),
        )
        self._previous = drawing
        return drawing

    def _flush(self) -> None:
        # Order within a layer is free, so each kind is prepared once for all its rows: the fixed
        # cost of the array operations is paid per frame instead of per catalog step.
        queued, self._queued = self._queued, defaultdict(list)
        draw: dict[str, Callable[..., None]] = {
            "boxes": self._draw_boxes,
            "lines": self._draw_lines,
            "circles": self._draw_circles,
            "points": self._draw_points,
            "polygons": self._draw_polygons,
            "polylines": self._draw_polylines,
            "texts": self._draw_texts,
        }
        for (kind, _), batches in queued.items():
            columns = [_join([arguments[index] for arguments, _ in batches]) for index in range(len(batches[0][0]))]
            paint = Paint.concatenate(
                [(len(cast(Sequence[object], arguments[0])), paint) for arguments, paint in batches]
            )
            draw[kind](*columns, paint)

    def boxes(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        w: NDArray[np.float64],
        h: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Queue normalized rectangles; zero-area rows draw nothing."""
        self._queued["boxes", 0].append(((x, y, w, h), paint))

    def lines(
        self,
        x1: NDArray[np.float64],
        y1: NDArray[np.float64],
        x2: NDArray[np.float64],
        y2: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Queue square-capped lines, including coincident endpoints."""
        self._queued["lines", 0].append(((x1, y1, x2, y2), paint))

    def circles(
        self, x: NDArray[np.float64], y: NDArray[np.float64], radius: NDArray[np.float64], paint: Paint
    ) -> None:
        """Queue circles with radius relative to the smaller target dimension."""
        self._queued["circles", 0].append(((x, y, radius), paint))

    def points(self, x: NDArray[np.float64], y: NDArray[np.float64], paint: Paint) -> None:
        """Queue square points following the stroke width."""
        self._queued["points", 0].append(((x, y), paint))

    def polylines(self, points: Sequence[Points] | NDArray[np.float64], paint: Paint) -> None:
        """Queue open polylines with bevel joins."""
        self._queued["polylines", 0].append(((list(points),), paint))

    def polygons(self, points: Sequence[Points] | NDArray[np.float64], paint: Paint) -> None:
        """Queue closed odd-even polygons, grouped by corner count."""
        if type(points) is np.ndarray:
            if points.shape[1] >= 3:
                self._queued["polygons", points.shape[1]].append(((points,), paint))
            return
        corners: dict[int, list[int]] = {}
        for row, shape in enumerate(points):
            if len(shape) >= 3:
                corners.setdefault(len(shape), []).append(row)
        for count, rows in corners.items():
            shapes = np.asarray([points[row] for row in rows], dtype=np.float64)
            self._queued["polygons", count].append(((shapes,), paint.take(np.array(rows))))

    def texts(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        text: Sequence[str],
        anchor: Sequence[str],
        paint: Paint,
    ) -> None:
        """Queue anchored, shaped text."""
        self._queued["texts", 0].append(((x, y, text, anchor), paint))

    def labels(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        anchor: Sequence[str],
        content: Sequence[LabelContent],
    ) -> None:
        """Paint each distinct label once and place it on whole device pixels, so its text stays crisp.

        Labels are prepared row by row, so unlike the other kinds they gain nothing from being queued.
        """
        s = self.settings
        for row, item in enumerate(content):
            if not item.runs:
                continue
            self._counts["label"] += 1
            sprite = label_sprite(item, s.scale, s.dpi, s.dpr)
            if sprite is None:
                continue
            fx, fy = _ANCHORS.get(str(anchor[row]), (0.0, 0.0))
            left = float(x[row]) * s.width - fx * sprite.width
            top = float(y[row]) * s.height - fy * sprite.height
            if not self._visible(left, top, sprite.width, sprite.height):
                continue
            position = QPointF(round(left * s.dpr) / s.dpr, round(top * s.dpr) / s.dpr)
            self._labels.append(LabelData(sprite, position))

    def _visible(self, x: float, y: float, width: float, height: float, stroke: float = 0.0) -> bool:
        if self._viewport is None:
            return True
        left, top, w, h = self._viewport
        margin = max(0.0, stroke) * 0.7071067811865476 + 2 / self.settings.dpr
        return (
            x - margin < left + w and x + width + margin > left and y - margin < top + h and y + height + margin > top
        )

    def _visible_rows(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        width: NDArray[np.float64],
        height: NDArray[np.float64],
        stroke: NDArray[np.float64],
    ) -> NDArray[np.bool_]:
        if self._viewport is None:
            return np.ones(len(x), dtype=np.bool_)
        left, top, w, h = self._viewport
        margin = np.maximum(0.0, stroke) * 0.7071067811865476 + 2 / self.settings.dpr
        return (
            (x - margin < left + w) & (x + width + margin > left) & (y - margin < top + h) & (y + height + margin > top)
        )

    def _draw_boxes(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        w: NDArray[np.float64],
        h: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Prepare visible rectangles: bulk vertices on hardware, Qt paths for dashed, narrow or rounded ones."""
        s = self.settings
        area = (w > 0) & (h > 0)
        self._counts["box"] += int(np.count_nonzero(area))
        count = len(x)
        stroke = np.maximum(0.0, _floats(paint.pen_width, count) * s.scale)
        left, top, width, height = x * s.width, y * s.height, w * s.width, h * s.height
        drawn = area & self._visible_rows(left, top, width, height, stroke)
        pixel = 1 / s.dpr
        radius = _floats(paint.radius, count) * s.scale
        mesh = (
            drawn
            & s.hardware
            & (radius <= 0)
            & ~np.broadcast_to(np.asarray(paint.dashed, dtype=np.bool_), (count,))
            & ~((stroke > 0) & (stroke < pixel))
            # Tolerate rounding: a one-pixel catalog length need not map back to exactly one pixel.
            & (np.minimum(width, height) >= stroke + pixel - 1e-6)
        )
        for row in np.flatnonzero(drawn & ~mesh):
            self.path(
                _rounded_rectangle_path(float(width[row]), float(height[row]), float(radius[row]))
                if radius[row] > 0
                else _rectangle_path(float(width[row]), float(height[row])),
                paint.style(int(row)),
                fill=True,
                origin=QPointF(left[row], top[row]),
            )
        rows = np.flatnonzero(mesh)
        if not rows.size:
            return
        fill = _colors(paint.brush_r, paint.brush_g, paint.brush_b, paint.brush_a, rows)
        pen = _colors(paint.pen_r, paint.pen_g, paint.pen_b, 255, rows)
        # Rows share a topology when stroke width and fill presence agree; usually all rows do.
        if type(paint.pen_width) is not np.ndarray and type(paint.brush_a) is not np.ndarray:
            unique = np.array([[stroke[0], float(np.asarray(paint.brush_a) > 0)]])
            groups = None
        else:
            filled = np.broadcast_to(np.asarray(paint.brush_a) > 0, (count,))
            unique, groups = np.unique(np.column_stack((stroke[rows], filled[rows])), axis=0, return_inverse=True)
        for index, (key_stroke, key_filled) in enumerate(unique):
            selected = np.arange(len(rows)) if groups is None else np.flatnonzero(groups == index)
            chosen = rows[selected]
            self._meshes.append(
                RectangleBatch(
                    rectangle_template(float(key_stroke), bool(key_filled), s.dpr),
                    width[chosen],
                    height[chosen],
                    left[chosen],
                    top[chosen],
                    None,
                    _select(fill, selected),
                    _select(pen, selected),
                )
            )

    def _draw_lines(
        self,
        x1: NDArray[np.float64],
        y1: NDArray[np.float64],
        x2: NDArray[np.float64],
        y2: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Prepare visible square-capped lines as rotated rectangles on hardware."""
        s = self.settings
        count = len(x1)
        self._counts["line"] += count
        same = (x1 == x2) & (y1 == y2)
        for row in np.flatnonzero(same):
            self._point(float(x1[row]), float(y1[row]), paint.style(int(row)))
        left, top = x1 * s.width, y1 * s.height
        dx, dy = (x2 - x1) * s.width, (y2 - y1) * s.height
        stroke = _floats(paint.pen_width, count) * s.scale
        drawn = ~same & self._visible_rows(
            np.minimum(x1, x2) * s.width, np.minimum(y1, y2) * s.height, np.abs(dx), np.abs(dy), stroke
        )
        length = np.hypot(dx, dy)
        mesh = (
            drawn
            & s.hardware
            & ~np.broadcast_to(np.asarray(paint.dashed, dtype=np.bool_), (count,))
            & (stroke >= 1 / s.dpr)
            & (length > 0)
        )
        for row in np.flatnonzero(drawn & ~mesh):
            self.path(
                f"m 0 0 l {dx[row]:.12g} {dy[row]:.12g}",
                paint.style(int(row)),
                fill=False,
                origin=QPointF(left[row], top[row]),
            )
        rows = np.flatnonzero(mesh)
        if not rows.size:
            return
        width, run, rise = stroke[rows], dx[rows], dy[rows]
        half = width / (2 * length[rows])
        color = _colors(paint.pen_r, paint.pen_g, paint.pen_b, 255, rows)
        self._meshes.append(
            RectangleBatch(
                rectangle_template(0.0, True, s.dpr),
                length[rows] + width,
                width,
                left[rows] + half * (rise - run),
                top[rows] - half * (run + rise),
                np.degrees(np.arctan2(rise, run)),
                color,
                color,
            )
        )

    def _draw_circles(
        self, x: NDArray[np.float64], y: NDArray[np.float64], radius: NDArray[np.float64], paint: Paint
    ) -> None:
        """Prepare circular Qt paths with conservative bounds."""
        s = self.settings
        for row in np.flatnonzero(radius > 0):
            self._counts["circle"] += 1
            r = float(radius[row]) * min(s.width, s.height)
            self.path(
                _circle_path(r),
                paint.style(int(row)),
                fill=True,
                origin=QPointF(x[row] * s.width, y[row] * s.height),
                bounds=QRectF(-r, -r, 2 * r, 2 * r),
            )

    def _draw_points(self, x: NDArray[np.float64], y: NDArray[np.float64], paint: Paint) -> None:
        """Prepare square points."""
        self._counts["point"] += len(x)
        for row in range(len(x)):
            self._point(float(x[row]), float(y[row]), paint.style(row))

    def _point(self, x: float, y: float, style: DrawingStyle) -> None:
        s = self.settings
        size = style.pen_width * s.scale
        if size <= 0:
            return
        fill = DrawingStyle(pen_width=0, brush_r=style.pen_r, brush_g=style.pen_g, brush_b=style.pen_b, brush_a=255)
        self.path(
            _rectangle_path(size, size),
            fill,
            fill=True,
            origin=QPointF(x * s.width - size / 2, y * s.height - size / 2),
            bounds=QRectF(0, 0, size, size),
        )

    def _draw_polygons(self, shapes: NDArray[np.float64], paint: Paint) -> None:
        """Prepare polygons of one corner count: convex ones as bulk vertices on hardware, others as Qt paths."""
        s = self.settings
        count = len(shapes)
        self._counts["polygon"] += count
        stroke = np.maximum(0.0, _floats(paint.pen_width, count) * s.scale)
        dashed = np.broadcast_to(np.asarray(paint.dashed, dtype=np.bool_), (count,))
        filled = np.broadcast_to(np.asarray(paint.brush_a) > 0, (count,))
        pixel = 1 / s.dpr
        pixels = shapes * (s.width, s.height)
        low, high = pixels.min(axis=1), pixels.max(axis=1)
        size = high - low
        visible = self._visible_rows(low[:, 0], low[:, 1], size[:, 0], size[:, 1], stroke)
        mesh = (
            visible
            & s.hardware
            & ~dashed
            & ~((stroke > 0) & (stroke < pixel))
            & convex_rows(pixels, np.where(stroke > 0, stroke + pixel, 1e-9))
        )
        for row in np.flatnonzero(visible & ~mesh):
            self._polygon(shapes[row], paint.style(int(row)), True)
        for fill in (True, False):
            chosen = np.flatnonzero(mesh & (filled == fill))
            if not chosen.size or (not fill and not np.any(stroke[chosen] > 0)):
                continue
            self._meshes.append(
                polygon_batch(
                    pixels[chosen],
                    stroke[chosen],
                    _colors(paint.brush_r, paint.brush_g, paint.brush_b, paint.brush_a, chosen),
                    _colors(paint.pen_r, paint.pen_g, paint.pen_b, 255, chosen),
                    fill,
                    s.dpr,
                )
            )

    def _draw_polylines(self, points: Sequence[Points | NDArray[np.float64]], paint: Paint) -> None:
        """Prepare connected segments with the standard Qt joins."""
        for row, shape in enumerate(points):
            if len(shape) >= 2:
                self._counts["polyline"] += 1
                self._polygon(shape, paint.style(row), False)

    def _polygon(self, points: Points | NDArray[np.float64], style: DrawingStyle, fill: bool) -> None:
        s = self.settings
        path = _poly_path(points, s.width, s.height)
        if fill:
            path = f"{path} z"
        self.path(
            path,
            style,
            fill=fill,
            origin=QPointF(points[0][0] * s.width, points[0][1] * s.height),
            bounds=_poly_bounds(points, s.width, s.height),
        )

    def path(
        self, path: str, style: DrawingStyle, *, fill: bool, origin: QPointF, bounds: QRectF | None = None
    ) -> None:
        """Prepare final Qt path properties; unknown bounds never cause rejection."""
        s = self.settings
        if bounds is not None and not self._visible(
            bounds.x() + origin.x(), bounds.y() + origin.y(), bounds.width(), bounds.height(), style.pen_width * s.scale
        ):
            return
        self._paths.append(
            PathData(
                path,
                origin,
                color(style.pen_r, style.pen_g, style.pen_b),
                color(style.brush_r, style.brush_g, style.brush_b, style.brush_a if fill else 0),
                style.pen_width * s.scale if style.pen_width > 0 else -1,
                style.pen_style == "dash",
            )
        )

    def _draw_texts(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        text: Sequence[str],
        anchor: Sequence[str],
        paint: Paint,
    ) -> None:
        """Shape once and prepare final glyph layouts and positions, preserving overhangs."""
        s = self.settings
        count = len(x)
        self._counts["text"] += count
        labels = [str(item) for item in text]
        anchors = [str(item) for item in anchor]
        sizes = (_floats(paint.font_size, count) * s.scale).tolist()
        family = paint.font_family
        families = [str(item) for item in family] if type(family) is np.ndarray else [str(family)] * count
        blocks = [_text_block(*key, s.dpi) for key in zip(labels, families, sizes)]
        widths = np.array([block.width for block in blocks])
        heights = np.array([block.height for block in blocks])
        baseline = np.array([item == "baseline" for item in anchors])
        factors = np.array([_ANCHORS.get(item, (0.0, 0.0)) for item in anchors]).reshape(-1, 2)
        left = x * s.width - factors[:, 0] * widths
        top = y * s.height - np.where(baseline, [block.ascent for block in blocks], factors[:, 1] * heights)
        visible = self._visible_rows(left, top, widths, heights, np.zeros(count)).tolist()
        channels = (paint.pen_r, paint.pen_g, paint.pen_b)
        if any(type(channel) is np.ndarray for channel in channels):
            rgb = np.stack([_floats(channel, count) for channel in channels], axis=1).astype(int).tolist()
            colors = [color(*channel) for channel in rgb]
        else:
            colors = [color(*(int(cast(float, channel)) for channel in channels))] * count
        for row in range(count):
            position = QPointF(left[row], top[row])
            if not visible[row]:
                ink = text_ink_bounds(labels[row], families[row], sizes[row], s.dpi)
                if not self._visible(ink.x() + position.x(), ink.y() + position.y(), ink.width(), ink.height()):
                    continue
            self._texts.append(TextData(blocks[row], position, colors[row]))
