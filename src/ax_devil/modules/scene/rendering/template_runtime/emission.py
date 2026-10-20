"""Emission kernels for generated catalog functions: point rows, deferred primitive batches and labels."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from ax_devil.modules.video_player.engine.drawing import (
    DrawingTarget,
    LabelColumns,
    Paint,
    Points,
    RunColumns,
)

from .kernels import RowState, objects_from
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


def _take_column(value: Any, selected: NDArray[np.intp]) -> Any:
    return value[selected] if type(value) is np.ndarray and value.ndim else value


def _take_values(value: Any, selected: NDArray[np.intp]) -> Sequence[object]:
    if type(value) is np.ndarray and value.ndim:
        return cast(Sequence[object], value[selected])
    return [value] * len(selected)


def emit(output: DrawingTarget, state: RowState) -> None:
    """Submit deferred primitive batches for rows that completed without failures.

    Each argument's spec code says how to select rows: numbers (n), point rows (p), other values (v),
    a column shared or per row (c), a paint (s) or label columns (l).
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
            else cast(LabelColumns, arg).take(selected)
            if code == "l"
            else _take_column(arg, selected)
            if code == "c"
            else _take_values(arg, selected)
            for code, arg in zip(spec, args)
        )
        getattr(output, kind)(*values)


def masked(value: Any, present: Any, fill: object) -> Any:
    """Replace the rows of a column that are absent with *fill*."""
    if type(present) is not np.ndarray:
        if present:
            return value
        return np.full(len(value), fill) if type(value) is np.ndarray else fill
    return np.where(present, value, fill)


def _hundredths(bar: float) -> float:
    """Clamp a bar to [0,1] and keep hundredths, finer than a bar's pixels, so similar values draw alike."""
    return bar if math.isnan(bar) else round(min(1.0, max(0.0, bar)), 2)


def _channel(value: Any, present: Any) -> Any:
    value = masked(value, present, 0)
    return value.astype(np.int64) if type(value) is np.ndarray else int(value)


def _strings(value: Any, present: Any, fill: str) -> Any:
    value = masked(value, present, fill)
    return value.astype(object) if type(value) is np.ndarray else value


def run_columns(
    text: Any,
    text_present: Any,
    bar: Any,
    bar_present: Any,
    red: Any,
    green: Any,
    blue: Any,
    weight: Any,
    present: Any,
) -> RunColumns:
    """Columns of one label run position from its resolved fields; absent parts are blank."""
    bar = masked(bar, bar_present, math.nan)
    if type(bar) is np.ndarray:
        bar = np.fromiter(map(_hundredths, np.clip(bar, 0.0, 1.0).tolist()), np.float64, len(bar))
    else:
        bar = _hundredths(float(bar))
    return RunColumns(
        _strings(text, text_present, ""),
        bar,
        _channel(red, present),
        _channel(green, present),
        _channel(blue, present),
        _strings(weight, present, "regular"),
        present if type(present) is np.ndarray else bool(present),
    )


def fill_columns(red: Any, green: Any, blue: Any, alpha: Any, present: Any) -> tuple[Any, Any, Any, Any]:
    """Red, green, blue and alpha columns of a label background; absent rows are transparent."""
    return _channel(red, present), _channel(green, present), _channel(blue, present), _channel(alpha, present)


def _run_row(run: object) -> tuple[Any, ...]:
    if run is None:
        return "", math.nan, 0, 0, 0, "regular", False
    fields = record(run)
    text, bar = fields.get("text"), fields.get("bar")
    red, green, blue = (int(cast(int, channel)) for channel in sequence(fields["color"]))
    filled = math.nan if bar is None else _hundredths(float(cast(float, bar)))
    return str(text or ""), filled, red, green, blue, fields["weight"], True


def run_records(runs: Any, present: Any = True) -> RunColumns:
    """Columns of one run position given whole records: one shared record or one per row (null allowed).

    *present* further limits which rows show the run.
    """
    if type(runs) is not np.ndarray:
        text, bar, red, green, blue, weight, shown = _run_row(runs)
        shown = shown & present if type(present) is np.ndarray else shown and bool(present)
        return RunColumns(text, bar, red, green, blue, weight, shown)
    rows = [_run_row(run) for run in runs.tolist()]
    columns = list(zip(*rows)) if rows else [()] * 7
    types: tuple[Any, ...] = (object, np.float64, np.int64, np.int64, np.int64, object, np.bool_)
    result = [np.array(column, dtype=kind) for column, kind in zip(columns, types)]
    text, bar, red, green, blue, weight, shown = result
    return RunColumns(text, bar, red, green, blue, weight, shown & present)


def collection_runs(collection: Any) -> tuple[RunColumns, ...]:
    """Columns of every run position of whole run lists: one shared list or one per row (null allowed)."""
    lists = [
        () if runs is None else sequence(runs)
        for runs in (collection.tolist() if type(collection) is np.ndarray else [collection])
    ]
    positions = max(1, max(map(len, lists), default=0))
    shared = type(collection) is not np.ndarray
    result = []
    for index in range(positions):
        items = [runs[index] if index < len(runs) else None for runs in lists]
        result.append(run_records(items[0] if shared else objects_from(items)))
    return tuple(result)
