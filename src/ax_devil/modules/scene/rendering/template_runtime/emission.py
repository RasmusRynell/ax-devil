"""Emission kernels for generated catalog functions: point rows, deferred primitive batches and labels."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from ax_devil.modules.video_player.engine.drawing import (
    DrawingTarget,
    LabelContent,
    LabelRun,
    LabelWeight,
    Paint,
    Points,
)

from .kernels import RowState
from .values import record, sequence


class PointRows:
    """Polygon points for every row: fixed items whose trailing ones may be absent, or per-row collections."""

    __slots__ = ("items", "size", "collections")

    def __init__(self, items: tuple[tuple[Any, Any], ...] = (), size: Any = None, collections: Any = None) -> None:
        self.items = items
        self.size = size
        self.collections = collections

    def rows(self, selected: NDArray[np.intp]) -> Sequence[Points] | NDArray[np.float64]:
        """Return the selected rows' points: one (rows, corners, 2) array when every row is complete."""
        if self.collections is not None:
            if type(self.collections) is np.ndarray:
                return [_pairs(self.collections[row]) for row in selected]
            return [_pairs(self.collections)] * len(selected)
        sizes = _take_numbers(len(self.items) if self.size is None else self.size, selected)
        coordinates = np.empty((len(selected), len(self.items), 2))
        for index, (x, y) in enumerate(self.items):
            coordinates[:, index, 0] = _take_numbers(x, selected)
            coordinates[:, index, 1] = _take_numbers(y, selected)
        if np.all(sizes == len(self.items)):
            return coordinates
        return [tuple(map(tuple, coordinates[row, : int(sizes[row])].tolist())) for row in range(len(selected))]


def _pairs(points: object) -> Points:
    return tuple((cast(float, record(point)["x"]), cast(float, record(point)["y"])) for point in sequence(points))


def _take_numbers(value: Any, selected: NDArray[np.intp]) -> NDArray[np.float64]:
    if type(value) is np.ndarray and value.ndim:
        return cast(NDArray[np.float64], value[selected].astype(np.float64, copy=False))
    return np.full(len(selected), float(value))


def _take_values(value: Any, selected: NDArray[np.intp]) -> Sequence[object]:
    if type(value) is np.ndarray and value.ndim:
        return cast(Sequence[object], value[selected])
    return [value] * len(selected)


def emit(output: DrawingTarget, state: RowState) -> None:
    """Submit deferred primitive batches for rows that completed without failures.

    Each argument's spec code says how to select rows: numbers (n), point rows (p), other values (v) or a paint (s).
    """
    completed = ~state.fail
    for kind, active, spec, args in state.pending:
        selected = np.flatnonzero(completed & active if type(active) is np.ndarray else completed if active else False)
        if not selected.size:
            continue
        values = tuple(
            _take_numbers(arg, selected)
            if code == "n"
            else cast(PointRows, arg).rows(selected)
            if code == "p"
            else cast(Paint, arg).take(selected)
            if code == "s"
            else _take_values(arg, selected)
            for code, arg in zip(spec, args)
        )
        getattr(output, kind)(*values)


def label_content(style: object, runs: object, active: bool) -> LabelContent | None:
    """Assemble a label from resolved lengths and runs, leaving inactive or failed rows untouched.

    Bars are clamped to [0,1] and kept to hundredths, finer than a bar's pixels, so labels showing
    similar values share their cached drawing."""
    if not active:
        return None
    label = record(style)

    fill = label["background"]
    background = None
    if fill is not None:
        red, green, blue = (int(cast(int, channel)) for channel in sequence(record(fill)["color"]))
        background = (red, green, blue, int(cast(int, record(fill)["alpha"])))
    kept = []
    for run in map(record, sequence(runs)):
        text, bar = run.get("text"), run.get("bar")
        if text or bar is not None:
            red, green, blue = (int(cast(int, channel)) for channel in sequence(run["color"]))
            filled = None if bar is None else round(min(1.0, max(0.0, float(cast(float, bar)))), 2)
            kept.append(LabelRun(str(text or ""), (red, green, blue), cast(LabelWeight, run["weight"]), filled))
    return LabelContent(
        tuple(kept),
        cast(float, label["size"]),
        str(label["family"] or ""),
        background,
        cast(float, label["padding_x"]),
        cast(float, label["padding_y"]),
        cast(float, label["radius"]),
        cast(float, label["gap"]),
    )
