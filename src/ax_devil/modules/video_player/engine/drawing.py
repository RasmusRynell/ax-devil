"""Drawing target contract and shared paint, anchor and label column types.

Coordinates are normalized [0,1] positions in the video frame. ``ANCHORS`` names the points of a text or label
rectangle that sit at the requested position, as fractions of its width and height.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray

ANCHORS: dict[str, tuple[float, float]] = {
    "top-left": (0, 0),
    "top-center": (0.5, 0),
    "top-right": (1, 0),
    "center-left": (0, 0.5),
    "center": (0.5, 0.5),
    "center-right": (1, 0.5),
    "bottom-left": (0, 1),
    "bottom-center": (0.5, 1),
    "bottom-right": (1, 1),
    "top": (0.5, 0),
    "bottom": (0.5, 1),
    "left": (0, 0.5),
    "right": (1, 0.5),
}


LabelWeight: TypeAlias = Literal["regular", "medium", "semibold", "bold"]


Column: TypeAlias = object
"""One value shared by every row, or an array with one value per row, like a ``Paint`` field."""


@dataclass(frozen=True, slots=True)
class RunColumns:
    """One run position of a batch of labels, a column per run field.

    An empty *text* is absent and a NaN *bar* is a text run; bars are clamped to [0,1] and kept to hundredths,
    finer than a bar's pixels. A row shows the run when it is *present* and has text or a bar.
    """

    text: Column
    bar: Column
    red: Column
    green: Column
    blue: Column
    weight: Column
    present: Column

    def take(self, rows: NDArray[np.intp]) -> RunColumns:
        """Select per-row values for the given rows; shared values stay shared."""
        return RunColumns(*(_take(getattr(self, name), rows) for name in _RUN_FIELDS))


_RUN_FIELDS = tuple(RunColumns.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class LabelColumns:
    """A batch of labels as columns.

    Lengths are relative to min(width,height). *runs* are laid out left to right, padded to the batch's
    longest label; *background* is red, green, blue and alpha, where alpha 0 means none.
    """

    runs: tuple[RunColumns, ...]
    size: Column
    family: Column
    background: tuple[Column, Column, Column, Column] | None
    padding_x: Column
    padding_y: Column
    radius: Column
    gap: Column

    def take(self, rows: NDArray[np.intp]) -> LabelColumns:
        """Select per-row values for the given rows; shared values stay shared."""
        background = None if self.background is None else tuple(_take(column, rows) for column in self.background)
        return LabelColumns(
            tuple(run.take(rows) for run in self.runs),
            _take(self.size, rows),
            _take(self.family, rows),
            cast("tuple[Column, Column, Column, Column] | None", background),
            _take(self.padding_x, rows),
            _take(self.padding_y, rows),
            _take(self.radius, rows),
            _take(self.gap, rows),
        )


_ABSENT_RUN = RunColumns("", math.nan, 0, 0, 0, "regular", False)


def concatenate_labels(parts: Sequence[tuple[int, LabelColumns]]) -> LabelColumns:
    """Join batches of labels row after row. Shorter labels gain absent runs; a missing background has alpha 0."""
    counts, items = [count for count, _ in parts], [labels for _, labels in parts]
    runs = tuple(
        RunColumns(
            *(
                concatenate_columns(
                    [
                        getattr(labels.runs[index] if index < len(labels.runs) else _ABSENT_RUN, name)
                        for labels in items
                    ],
                    counts,
                )
                for name in _RUN_FIELDS
            )
        )
        for index in range(max(len(labels.runs) for labels in items))
    )
    background = None
    if any(labels.background is not None for labels in items):
        background = tuple(
            concatenate_columns(
                [0 if labels.background is None else labels.background[channel] for labels in items], counts
            )
            for channel in range(4)
        )

    def join(name: str) -> object:
        return concatenate_columns([getattr(labels, name) for labels in items], counts)

    return LabelColumns(
        runs,
        join("size"),
        join("family"),
        cast("tuple[Column, Column, Column, Column] | None", background),
        join("padding_x"),
        join("padding_y"),
        join("radius"),
        join("gap"),
    )


Points: TypeAlias = tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class Paint:
    """Drawing style for a batch of rows.

    Each field is either one value shared by every row or an array with one value per row.
    Colors are in [0,255]; widths, radii and font sizes are relative to min(width,height).
    """

    pen_r: object = 255
    pen_g: object = 0
    pen_b: object = 0
    pen_width: object = 0.002
    dashed: object = False
    brush_r: object = 0
    brush_g: object = 0
    brush_b: object = 0
    brush_a: object = 0
    font_family: object = "Arial"
    font_size: object = 0.02
    radius: object = 0.0

    @classmethod
    def concatenate(cls, parts: Sequence[tuple[int, Paint]]) -> Paint:
        """Join batches' rows in order; values shared by every part stay shared."""
        fields = []
        for name in _PAINT_FIELDS:
            fields.append(
                concatenate_columns([getattr(paint, name) for _, paint in parts], [count for count, _ in parts])
            )
        return cls(*fields)

    def take(self, rows: NDArray[np.intp]) -> Paint:
        """Select per-row values for the given rows; shared values stay shared."""
        return Paint(*(_take(getattr(self, name), rows) for name in _PAINT_FIELDS))


_PAINT_FIELDS = tuple(Paint.__dataclass_fields__)


def concatenate_columns(values: Sequence[object], counts: Sequence[int]) -> object:
    """Join the columns of consecutive batches; a value shared by every batch stays shared."""
    first = values[0]
    if all(type(value) is not np.ndarray and value == first for value in values):
        return first
    return np.concatenate([np.broadcast_to(np.asarray(value), (count,)) for value, count in zip(values, counts)])


def _take(value: object, rows: NDArray[np.intp]) -> object:
    return value[rows] if type(value) is np.ndarray else value


class DrawingTarget(Protocol):
    """Consume batches of drawing instructions; row i of every argument describes one primitive.

    Coordinates are normalized to the target image. Numeric arguments are float arrays of equal length.
    """

    def boxes(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        w: NDArray[np.float64],
        h: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Prepare normalized rectangles; zero-area rows draw nothing."""

    def circles(
        self, x: NDArray[np.float64], y: NDArray[np.float64], radius: NDArray[np.float64], paint: Paint
    ) -> None:
        """Prepare circles with radius relative to the smaller target dimension."""

    def lines(
        self,
        x1: NDArray[np.float64],
        y1: NDArray[np.float64],
        x2: NDArray[np.float64],
        y2: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Prepare square-capped lines, including coincident endpoints."""

    def points(self, x: NDArray[np.float64], y: NDArray[np.float64], paint: Paint) -> None:
        """Prepare square points following the stroke width."""

    def polygons(self, points: Sequence[Points] | NDArray[np.float64], paint: Paint) -> None:
        """Prepare closed odd-even polygons; rows with equal corner counts may come as one (rows, corners, 2) array."""

    def polylines(self, points: Sequence[Points] | NDArray[np.float64], paint: Paint) -> None:
        """Prepare open polylines with round joins, given like polygon points."""

    def texts(
        self, x: NDArray[np.float64], y: NDArray[np.float64], text: Sequence[str], anchor: Sequence[str], paint: Paint
    ) -> None:
        """Prepare anchored, shaped text."""

    def labels(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        anchor: Column,
        content: LabelColumns,
    ) -> None:
        """Prepare labels; *anchor* (one name or one per row) places each label's background at its point."""
