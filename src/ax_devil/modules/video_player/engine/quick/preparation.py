"""Overlay quad preparation from one frame of drawing batches, for display and export."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage

from ax_devil.modules.settings.logging_config import get_logger

from ..drawing import LabelColumns, Paint, Points, concatenate_columns, concatenate_labels
from ..render_context import RenderContext
from .glyphs import AtlasFull, GlyphAtlas, glyph_atlas
from .label_layout import LabelQuads, label_quads, text_primitive_quads, visible_rows
from .overlay_vertex import OVERLAY_VERTEX, per_row
from .shape_quads import box_quads, circle_quads, line_quads, point_quads, polygon_quads, polyline_quads


@dataclass(frozen=True, slots=True)
class DrawingSettings:
    """Exact display/export geometry and font inputs, separate from catalog units."""

    width: float
    height: float
    scale: float
    dpi: int = 96
    dpr: float = 1.0
    viewport: tuple[float, float, float, float] | None = None

    def culling(self) -> tuple[float, float, float, float] | None:
        """The viewport, letterbox included, grown by half its size on each side; None draws everything."""
        if self.viewport is None:
            return None
        left, top, width, height = self.viewport
        return (left - width / 2, top - height / 2, width * 2, height * 2)

    def covers(self, other: DrawingSettings) -> bool:
        """Whether a drawing prepared with these settings also holds all that *other* shows.

        True when every field but the viewport is equal and the other viewport lies inside
        :meth:`culling`, so panning within the margin reuses the prepared drawing.
        """
        if replace(self, viewport=None) != replace(other, viewport=None):
            return False
        culling = self.culling()
        if culling is None:
            return True
        if other.viewport is None:
            return False
        left, top, width, height = other.viewport
        cx, cy, cw, ch = culling
        return cx <= left and cy <= top and left + width <= cx + cw and top + height <= cy + ch

    @classmethod
    def for_context(cls, context: RenderContext) -> DrawingSettings:
        """Construct uncropped preparation settings for a logical target."""
        return cls(context.width, context.height, context.scale_factor)


@dataclass(frozen=True, slots=True, eq=False)
class PreparedDrawing:
    """Immutable submission data: one stream of overlay quads and the atlas page their glyphs sample.

    *vertices* are ``OVERLAY_VERTEX``, four per quad, drawn in order as shapes, texts, then labels, so text stays
    readable. Order across entities is not preserved; within a kind emission order is kept. *drawn* counts the
    shapes, texts and labels in them. Identity is meaningful: a drawing is never modified, so the same object
    means the same pixels.
    """

    vertices: NDArray[np.void] = field(default_factory=lambda: np.zeros(0, OVERLAY_VERTEX))
    page: QImage | None = None
    counts: tuple[tuple[str, int], ...] = ()
    drawn: int = 0

    def __len__(self) -> int:
        return self.drawn


EMPTY_DRAWING = PreparedDrawing()
logger = get_logger(__name__)
_warned_dropped_glyphs = False
_EMPTY_QUADS = LabelQuads(np.zeros((0, 4), OVERLAY_VERTEX), 0)


def _warn_dropped_glyphs(count: int) -> None:
    """Log, once per process, that a frame's text needed more glyph space than the atlas page holds."""
    global _warned_dropped_glyphs
    if not _warned_dropped_glyphs:
        _warned_dropped_glyphs = True
        logger.warning(f"Glyph atlas page is full: {count} glyphs were left out of a frame")


def _join(values: list[object]) -> object:
    """Concatenate per-row arguments of several batches."""
    if all(type(value) is np.ndarray for value in values):
        return np.concatenate(cast(list[NDArray[np.generic]], values))
    return [item for value in values for item in cast(Sequence[object], value)]


class DrawingBuffer:
    """Collect one frame of compiled catalog instructions and prepare its overlay quads."""

    def __init__(self, settings: DrawingSettings) -> None:
        self.settings = settings
        self._culling = settings.culling()
        self._shapes: list[NDArray[np.void]] = []
        self._drawn_shapes = 0
        self._texts: list[
            tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.object_], NDArray[np.object_], Paint]
        ] = []
        self._labels: list[tuple[NDArray[np.float64], NDArray[np.float64], object, LabelColumns]] = []
        self._counts: Counter[str] = Counter()
        self._queued: defaultdict[tuple[str, int], list[tuple[tuple[object, ...], Paint]]] = defaultdict(list)

    def finish(self) -> PreparedDrawing:
        """Prepare every queued batch, then publish without retaining mutable builder collections."""
        self._flush()
        shapes, self._shapes = self._shapes, []
        texts, self._texts = self._texts, []
        labels, self._labels = self._labels, []
        parts = list(shapes)
        drawn = self._drawn_shapes
        page = None
        if texts or labels:
            atlas = glyph_atlas(self.settings.dpi, self.settings.dpr)
            try:
                text, label = self._text_and_labels(texts, labels, atlas)
            except AtlasFull:  # The page filled up: start a fresh one and lay out everything once more.
                atlas.reset()
                atlas.drop_overflow = True  # Even a fresh page is too small: draw the glyphs that fit.
                text, label = self._text_and_labels(texts, labels, atlas)
            self._counts["label"] += label.labels
            parts += [text.vertices.reshape(-1), label.vertices.reshape(-1)]
            drawn += text.labels + label.labels
            page = atlas.snapshot()
            if atlas.dropped:
                _warn_dropped_glyphs(atlas.dropped)
                atlas.reset()  # Later frames must not reuse strings shaped with missing glyphs.
        vertices = np.concatenate(parts) if parts else np.zeros(0, OVERLAY_VERTEX)
        vertices.flags.writeable = False
        return PreparedDrawing(vertices, page, tuple(sorted(self._counts.items())), drawn)

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
        """Queue open polylines with round joins."""
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
        anchor: object,
        content: LabelColumns,
    ) -> None:
        """Queue labels; they are laid out on the glyph atlas when the frame finishes."""
        self._labels.append((x, y, anchor, content))

    def _text_and_labels(
        self,
        texts: list[tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.object_], NDArray[np.object_], Paint]],
        labels: list[tuple[NDArray[np.float64], NDArray[np.float64], object, LabelColumns]],
        atlas: GlyphAtlas,
    ) -> tuple[LabelQuads, LabelQuads]:
        """Lay out text primitives and labels on *atlas*; raises AtlasFull when its page is too small."""
        return self._text_quads(texts, atlas), self._label_quads(labels, atlas)

    def _text_quads(
        self,
        queued: list[tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.object_], NDArray[np.object_], Paint]],
        atlas: GlyphAtlas,
    ) -> LabelQuads:
        """Glyph quads of the queued text primitives."""
        batches = [
            text_primitive_quads(x, y, text, anchor, paint, self.settings, atlas)
            for x, y, text, anchor, paint in queued
        ]
        if not batches:
            return _EMPTY_QUADS
        return LabelQuads(np.concatenate([batch.vertices for batch in batches]), sum(batch.labels for batch in batches))

    def _label_quads(
        self, queued: list[tuple[NDArray[np.float64], NDArray[np.float64], object, LabelColumns]], atlas: GlyphAtlas
    ) -> LabelQuads:
        """Lay out the queued labels, merged into one batch so that their drawing order is kept."""
        if not queued:
            return _EMPTY_QUADS
        counts = [len(x) for x, _, _, _ in queued]
        x, y = (np.concatenate([call[index] for call in queued]) for index in (0, 1))
        anchor = concatenate_columns([anchor for _, _, anchor, _ in queued], counts)
        content = concatenate_labels([(count, labels) for count, (_, _, _, labels) in zip(counts, queued)])
        return label_quads(x, y, anchor, content, self.settings, atlas)

    def _visible_rows(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        width: NDArray[np.float64],
        height: NDArray[np.float64],
        stroke: NDArray[np.float64],
    ) -> NDArray[np.bool_]:
        return visible_rows(self._culling, self.settings.dpr, x, y, width, height, stroke)

    def _add_shapes(
        self,
        make: Callable[..., NDArray[np.void]],
        visible: NDArray[np.bool_],
        columns: tuple[NDArray[np.float64], ...],
        paint: Paint,
        *extra: float,
    ) -> None:
        """Queue the quads of the visible rows (one shape per quad); rows are culled before any quad is built."""
        s = self.settings
        if not visible.all():
            rows = np.flatnonzero(visible)
            columns, paint = tuple(column[rows] for column in columns), paint.take(rows)
        if len(columns[0]):
            quads = make(*columns, paint, s.width, s.height, s.scale, *extra)
            self._shapes.append(quads)
            self._drawn_shapes += len(quads) // 4

    def _draw_boxes(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        w: NDArray[np.float64],
        h: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Prepare visible rectangles as overlay quads; zero-area rows draw nothing."""
        s = self.settings
        self._counts["box"] += int(np.count_nonzero((w > 0) & (h > 0)))
        stroke = np.maximum(0.0, per_row(paint.pen_width, len(x)) * s.scale)
        visible = self._visible_rows(x * s.width, y * s.height, w * s.width, h * s.height, stroke)
        self._add_shapes(box_quads, visible, (x, y, w, h), paint)

    def _draw_lines(
        self,
        x1: NDArray[np.float64],
        y1: NDArray[np.float64],
        x2: NDArray[np.float64],
        y2: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Prepare visible square-capped lines as rotated overlay quads."""
        s = self.settings
        self._counts["line"] += len(x1)
        stroke = np.maximum(0.0, per_row(paint.pen_width, len(x1)) * s.scale)
        visible = self._visible_rows(
            np.minimum(x1, x2) * s.width,
            np.minimum(y1, y2) * s.height,
            np.abs(x2 - x1) * s.width,
            np.abs(y2 - y1) * s.height,
            stroke,
        )
        self._add_shapes(line_quads, visible, (x1, y1, x2, y2), paint)

    def _draw_circles(
        self, x: NDArray[np.float64], y: NDArray[np.float64], radius: NDArray[np.float64], paint: Paint
    ) -> None:
        """Prepare visible circles as overlay quads."""
        s = self.settings
        self._counts["circle"] += int(np.count_nonzero(radius > 0))
        r = radius * min(s.width, s.height)
        stroke = np.maximum(0.0, per_row(paint.pen_width, len(x)) * s.scale)
        visible = self._visible_rows(x * s.width - r, y * s.height - r, 2 * r, 2 * r, stroke)
        self._add_shapes(circle_quads, visible, (x, y, radius), paint)

    def _draw_points(self, x: NDArray[np.float64], y: NDArray[np.float64], paint: Paint) -> None:
        """Prepare visible square points as overlay quads."""
        s = self.settings
        self._counts["point"] += len(x)
        size = np.maximum(0.0, per_row(paint.pen_width, len(x)) * s.scale)
        visible = self._visible_rows(x * s.width - size / 2, y * s.height - size / 2, size, size, np.zeros(len(x)))
        self._add_shapes(point_quads, visible, (x, y), paint)

    def _draw_polygons(self, shapes: NDArray[np.float64], paint: Paint) -> None:
        """Prepare polygons of one corner count: visible rows become fills and outlines."""
        s = self.settings
        self._counts["polygon"] += len(shapes)
        stroke = np.maximum(0.0, per_row(paint.pen_width, len(shapes)) * s.scale)
        pixels = shapes * (s.width, s.height)
        low, high = pixels.min(axis=1), pixels.max(axis=1)
        visible = self._visible_rows(low[:, 0], low[:, 1], high[:, 0] - low[:, 0], high[:, 1] - low[:, 1], stroke)
        rows = np.flatnonzero(visible)
        if rows.size:
            self._shapes.append(polygon_quads(shapes[rows], paint.take(rows), s.width, s.height, s.scale, s.dpr))
            self._drawn_shapes += rows.size

    def _draw_polylines(self, points: Sequence[Points | NDArray[np.float64]], paint: Paint) -> None:
        """Prepare the visible polylines of two or more points as round-jointed segments."""
        s = self.settings
        shown = [row for row, shape in enumerate(points) if len(shape) >= 2]
        self._counts["polyline"] += len(shown)
        shapes = [np.asarray(points[row], dtype=np.float64) for row in shown]
        stroke = np.maximum(0.0, per_row(paint.pen_width, len(points)) * s.scale)[shown]
        low = np.array([shape.min(axis=0) for shape in shapes]).reshape(-1, 2) * (s.width, s.height)
        high = np.array([shape.max(axis=0) for shape in shapes]).reshape(-1, 2) * (s.width, s.height)
        visible = self._visible_rows(low[:, 0], low[:, 1], high[:, 0] - low[:, 0], high[:, 1] - low[:, 1], stroke)
        rows = np.flatnonzero(visible)
        if rows.size:
            chosen = np.array(shown)[rows]
            self._shapes.append(
                polyline_quads([shapes[row] for row in rows], paint.take(chosen), s.width, s.height, s.scale)
            )
            self._drawn_shapes += rows.size

    def _draw_texts(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        text: Sequence[str],
        anchor: Sequence[str],
        paint: Paint,
    ) -> None:
        """Queue text for the atlas layout that finish() performs together with the labels."""
        self._counts["text"] += len(x)
        columns = (np.asarray(text, dtype=object), np.asarray(anchor, dtype=object))
        self._texts.append((x, y, *columns, paint))
