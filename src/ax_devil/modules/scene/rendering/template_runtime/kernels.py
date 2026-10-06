"""Row-column runtime for generated catalog functions.

A value is either one Python value shared by every row or a NumPy array with one entry per row.
Helpers are total: inactive and null rows hold placeholders and never raise here. Generated code
checks results per row and reports failures through ``fire``; failed rows emit nothing.
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache, reduce
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
from ax_devil.modules.video_player.engine.render_context import RenderContext

from .values import TemplateRuntimeError, record, sequence

Check = tuple[str, str, str]
ANCHORS = {
    "top-left": (0.0, 0.0),
    "top-center": (0.5, 0.0),
    "top-right": (1.0, 0.0),
    "center-left": (0.0, 0.5),
    "center": (0.5, 0.5),
    "center-right": (1.0, 0.5),
    "bottom-left": (0.0, 1.0),
    "bottom-center": (0.5, 1.0),
    "bottom-right": (1.0, 1.0),
    "top": (0.5, 0.0),
    "bottom": (0.5, 1.0),
    "left": (0.0, 0.5),
    "right": (1.0, 0.5),
}


class RowState:
    """One invocation's failed rows, first-failure check order and deferred primitive batches."""

    __slots__ = ("fail", "hits", "pending")

    def __init__(self, count: int) -> None:
        self.fail = np.zeros(count, dtype=np.bool_)
        self.hits: list[Check] = []
        self.pending: list[tuple[str, object, str, tuple[object, ...]]] = []

    def first_error(self) -> TemplateRuntimeError:
        """Return the first reported check as a located error."""
        code, location, message = self.hits[0]
        return TemplateRuntimeError(message, code=code, location=location)


def fire(state: RowState, check: Check, rows: object, location: str | None = None) -> None:
    """Fail the given rows (a mask, or a truthy value for every row) unless they already failed."""
    fail = state.fail
    if type(rows) is np.ndarray:
        selected = rows & ~fail
    elif rows:
        selected = ~fail
    else:
        return
    if not selected.any():
        return
    fail |= selected
    if location is not None and check[1] == "$":
        check = (check[0], location, check[2])
    if check not in state.hits:
        state.hits.append(check)


def relay(
    state: RowState,
    target: list[tuple[object, Check]] | None,
    terms: Sequence[tuple[object, Check]],
    active: object,
    location: str,
) -> None:
    """Report a memoized value's failures for the rows that demand it here."""
    for rows, check in terms:
        demanded = active if rows is True else rows if active is True else cast(Any, rows) & active
        if check[1] == "$":
            check = (check[0], location, check[2])
        if target is None:
            fire(state, check, demanded)
        else:
            target.append((demanded, check))


def bad(ok: object, active: object) -> object:
    """Rows among the active ones where a check fails, or False when none fail."""
    if type(ok) is np.ndarray:
        if ok.all():
            return False
        return ~ok if active is True else ~ok & active
    return False if ok else active


def failure(error: Exception, location: str) -> Check:
    """Describe an exception raised by a shared-value validator as a located check."""
    if isinstance(error, TemplateRuntimeError):
        diagnostic = error.diagnostic
        return diagnostic.code, diagnostic.location if diagnostic.location != "$" else location, diagnostic.message
    return "evaluation_error", location, str(error)


def any_rows(mask: object) -> bool:
    """Return whether any row is selected."""
    return bool(mask.any()) if type(mask) is np.ndarray else bool(mask)


def invert(mask: object) -> object:
    """Negate a row mask or a shared condition."""
    return ~mask if type(mask) is np.ndarray else not mask


def select(condition: object, then: object, other: object) -> object:
    """Choose per row; a shared condition chooses a whole value."""
    if type(condition) is not np.ndarray:
        return then if condition else other
    if type(then) is tuple or type(other) is tuple:
        then, other = objects(then, len(condition)), objects(other, len(condition))
    return np.where(condition, cast(Any, then), cast(Any, other))


def objects(value: object, count: int) -> NDArray[np.object_]:
    """Return per-row Python objects, repeating a shared value."""
    if type(value) is np.ndarray and value.ndim:
        return cast(NDArray[np.object_], value)
    result = np.empty(count, dtype=object)
    for index in range(count):
        result[index] = value
    return result


def finite(value: object) -> object:
    """Return whether numbers are finite, per row."""
    if type(value) is np.ndarray:
        return np.isfinite(value)
    try:
        return math.isfinite(cast(float, value))
    except (OverflowError, TypeError):
        return False


def minimum(*values: Any) -> Any:
    """Smallest value per row."""
    if any(type(value) is np.ndarray for value in values):
        return reduce(np.minimum, values)
    return min(values)


def maximum(*values: Any) -> Any:
    """Largest value per row."""
    if any(type(value) is np.ndarray for value in values):
        return reduce(np.maximum, values)
    return max(values)


def divide(numerator: Any, denominator: Any) -> Any:
    """Divide, leaving placeholders where the denominator is zero; generated code reports those rows."""
    if type(numerator) is np.ndarray or type(denominator) is np.ndarray:
        return np.divide(numerator, denominator)
    return numerator / denominator if denominator else 0.0


def unit_scale(unit: Any, context: RenderContext) -> Any:
    """Pixels per unit of a length."""
    if type(unit) is np.ndarray:
        return np.select(
            (unit == "px", unit == "image_width", unit == "image_height"),
            (1.0, float(context.width), float(context.height)),
            float(context.scale_factor),
        )
    if unit == "px":
        return 1.0
    return (
        context.width if unit == "image_width" else context.height if unit == "image_height" else context.scale_factor
    )


def anchor_factors(anchor: Any) -> tuple[Any, Any]:
    """Horizontal and vertical anchor fractions."""
    if type(anchor) is np.ndarray:
        pairs = [ANCHORS.get(str(item), (0.0, 0.0)) for item in anchor.tolist()]
        return np.array([pair[0] for pair in pairs]), np.array([pair[1] for pair in pairs])
    return ANCHORS.get(anchor, (0.0, 0.0))


def nonempty(value: Any) -> Any:
    """Whether text or collections contain anything, per row."""
    if type(value) is np.ndarray:
        if value.dtype == object:
            return np.fromiter((bool(item) for item in value), dtype=np.bool_, count=len(value))
        return np.char.str_len(value) > 0
    return bool(value)


def lengths(value: Any) -> Any:
    """Item count of collections, per row."""
    if type(value) is np.ndarray:
        return np.fromiter((len(item) for item in value), dtype=np.intp, count=len(value))
    return len(value)


def isin(value: Any, choices: tuple[str, ...]) -> Any:
    """Whether text is one of the allowed choices, per row."""
    if type(value) is np.ndarray:
        return np.fromiter((item in choices for item in value.tolist()), dtype=np.bool_, count=len(value))
    return value in choices


def per_row(function: Callable[..., object], *args: Any) -> Any:
    """Apply a scalar function per row; shared arguments call it once."""
    arrays = [arg for arg in args if type(arg) is np.ndarray and arg.ndim]
    if not arrays:
        return function(*args)
    count = len(arrays[0])
    columns = [arg.tolist() if type(arg) is np.ndarray and arg.ndim else [arg] * count for arg in args]
    return objects_from(function(*row) for row in zip(*columns))


def objects_from(values: Any) -> NDArray[np.object_]:
    """Collect Python objects into a per-row array without unpacking sequences."""
    items = list(values)
    result = np.empty(len(items), dtype=object)
    for index, item in enumerate(items):
        result[index] = item
    return result


def arrow(
    ox: Any,
    oy: Any,
    vx: Any,
    vy: Any,
    gain: Any,
    maximum_length: Any,
    head: Any,
    half: Any,
    threshold: Any,
    width: float,
    height: float,
) -> tuple[Any, ...]:
    """Arrow endpoint and head points per row.

    Returns presence, finite-ness, end x/y, both head base points and whether a head is drawn.
    Rows at or below the magnitude threshold, or with zero gain, length or magnitude, have no arrow.
    """
    shared = not any(type(value) is np.ndarray for value in (ox, oy, vx, vy, gain, maximum_length, head, half))
    with np.errstate(all="ignore"):
        dx = np.multiply(np.multiply(vx, width), gain)
        dy = np.multiply(np.multiply(vy, height), gain)
        magnitude = np.hypot(dx, dy)
        present = ~((np.hypot(vx, vy) <= threshold) | np.equal(gain, 0) | np.equal(maximum_length, 0))
        ok = ~present | (np.isfinite(dx) & np.isfinite(dy) & np.isfinite(magnitude))
        present = present & (magnitude != 0)
        safe = np.where(magnitude == 0, 1.0, magnitude)
        ux, uy = dx / safe, dy / safe
        shaft = np.minimum(magnitude, maximum_length)
        ex, ey = ox + ux * shaft / width, oy + uy * shaft / height
        headed = np.greater(head, 0) & np.greater(half, 0)
        fraction = np.minimum(1.0, shaft / np.where(np.greater(head, 0), head, 1.0))
        length, spread = np.multiply(head, fraction), np.multiply(half, fraction)
        p1x = ex + (-ux * length - uy * spread) / width
        p1y = ey + (-uy * length + ux * spread) / height
        p2x = ex + (-ux * length + uy * spread) / width
        p2y = ey + (-uy * length - ux * spread) / height
        points = np.isfinite(ex) & np.isfinite(ey) & (~headed | (np.isfinite(p1x) & np.isfinite(p1y)))
        ok = ok & (~present | (points & (~headed | (np.isfinite(p2x) & np.isfinite(p2y)))))
    result = (present, ok, ex, ey, p1x, p1y, p2x, p2y, headed)
    if shared:
        return tuple(bool(value) if index in (0, 1, 8) else float(value) for index, value in enumerate(result))
    return result


def ramp(value: Any, positions: NDArray[np.float64], colors: NDArray[np.float64]) -> tuple[Any, Any, Any]:
    """Interpolate constant color stops per row, rounding channels half up."""
    if type(value) is not np.ndarray:
        result = _ramp(float(value), tuple(positions.tolist()), tuple(map(tuple, colors.tolist())))
        return result[0], result[1], result[2]
    index = np.searchsorted(positions, value, side="left")
    segment = np.clip(index, 1, len(positions) - 1)
    low, high = positions[segment - 1], positions[segment]
    scale = np.maximum(np.maximum(np.abs(low), np.abs(high)), 1.0)
    with np.errstate(all="ignore"):
        t = ((value / scale - low / scale) / (high / scale - low / scale))[:, None]
    channels = np.floor(colors[segment - 1] * (1 - t) + colors[segment] * t + 0.5)
    channels[value <= positions[0]] = colors[0]
    channels[index >= len(positions)] = colors[-1]
    return channels[:, 0], channels[:, 1], channels[:, 2]


@lru_cache(maxsize=4096)
def _ramp(value: float, positions: tuple[float, ...], colors: tuple[tuple[float, ...], ...]) -> tuple[int, ...]:
    if value <= positions[0]:
        return tuple(int(channel) for channel in colors[0])
    for index in range(1, len(positions)):
        low, high = positions[index - 1], positions[index]
        if value > high:
            continue
        # Weighted normalization avoids overflow when finite endpoints straddle zero.
        scale = max(abs(low), abs(high), 1.0)
        t = (value / scale - low / scale) / (high / scale - low / scale)
        return tuple(math.floor(a * (1 - t) + b * t + 0.5) for a, b in zip(colors[index - 1], colors[index]))
    return tuple(int(channel) for channel in colors[-1])


def ramp_table(stops: object) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Resolve validated color stops into position and color arrays."""
    items = [record(stop) for stop in sequence(stops)]
    return (
        np.array([cast(float, item["at"]) for item in items], dtype=np.float64),
        np.array([list(sequence(item["color"])) for item in items], dtype=np.float64),
    )


def ramp_rows(value: Any, stops: object) -> tuple[Any, Any, Any]:
    """Interpolate color stops that are not constant."""
    rows = per_row(_ramp_stops, value, stops)
    if type(rows) is not np.ndarray:
        return rows[0], rows[1], rows[2]
    channels = np.array(rows.tolist(), dtype=np.float64).reshape(-1, 3)
    return channels[:, 0], channels[:, 1], channels[:, 2]


def _ramp_stops(value: float, stops: object) -> tuple[int, ...]:
    # Inactive rows can carry null or empty collection placeholders.
    if not stops:
        return (0, 0, 0)
    positions, colors = ramp_table(stops)
    return _ramp(float(value), tuple(positions.tolist()), tuple(map(tuple, colors.tolist())))


def palette_table(colors: object) -> tuple[tuple[int, ...], ...]:
    """Resolve validated palette colors into channel tuples."""
    return tuple(tuple(int(cast(int, channel)) for channel in sequence(color)) for color in sequence(colors))


def pick_color(text: Any, table: tuple[tuple[int, ...], ...]) -> tuple[Any, Any, Any]:
    """Pick a color for text from a constant palette, per row; the same text always gets the same color, in every
    run."""
    return _pick_channels(per_row(lambda item: _pick(str(item), table), text))


def pick_color_rows(text: Any, colors: object) -> tuple[Any, Any, Any]:
    """Pick colors from a palette that is not constant."""
    return _pick_channels(per_row(lambda item, palette: _pick(str(item), _palette(palette)), text, colors))


def _palette(colors: object) -> tuple[tuple[int, ...], ...]:
    # Inactive rows can carry null or empty collection placeholders.
    return palette_table(colors) if colors else ((0, 0, 0),)


def _pick_channels(rows: Any) -> tuple[Any, Any, Any]:
    if type(rows) is not np.ndarray:
        return rows[0], rows[1], rows[2]
    channels = np.array(rows.tolist(), dtype=np.int64).reshape(-1, 3)
    return channels[:, 0], channels[:, 1], channels[:, 2]


@lru_cache(maxsize=8192)
def _pick(text: str, colors: tuple[tuple[int, ...], ...]) -> tuple[int, ...]:
    # crc32, not hash(): Python salts str hashes per process, and a track must keep its color across runs.
    return colors[zlib.crc32(text.encode()) % len(colors)]


@lru_cache(maxsize=8192)
def _trim(text: str, count: int, delimiter: str | None, suffix: str, middle: bool) -> str:
    if middle:
        # Both ends survive, so ids that differ only at their start or end stay apart.
        head = (count + 1) // 2
        return f"{text[:head]}{suffix}{text[len(text) - count + head :]}" if len(text) > count else text
    result = text[:count]
    if delimiter:
        result = result.split(delimiter)[0]
    return f"{result}{suffix}" if result != text else result


def trim_text(text: Any, count: Any, delimiter: Any, suffix: Any, elide: Any) -> Any:
    """Shorten text to a length, marking shortened text with a suffix at its end or, elided in the middle, between
    its first and last characters; the delimiter cuts only text shortened at the end."""
    return per_row(
        lambda *row: _trim(str(row[0]), max(0, int(row[1])), row[2] or None, str(row[3]), row[4] == "middle"),
        text,
        count,
        delimiter,
        suffix,
        elide,
    )


def text_table(mapping: object) -> dict[str, str]:
    """Resolve validated text mapping entries into a dictionary."""
    return {cast(str, record(entry)["key"]): cast(str, record(entry)["text"]) for entry in sequence(mapping)}


def lookup_rows(text: Any, mapping: Any, default: Any) -> Any:
    """Map text through a mapping that is not constant; unmatched text uses the default."""
    return per_row(lambda *row: text_table(row[1]).get(row[0], row[2]), text, mapping, default)


def number_text(value: Any, precision: Any, hide_zero: Any) -> tuple[Any, Any]:
    """Format numbers with fixed decimals; zero is absent when hidden."""
    visible = (
        ~(np.asarray(hide_zero) & np.equal(value, 0)) if type(value) is np.ndarray else not (hide_zero and value == 0)
    )
    text = per_row(lambda number, places: f"{number:.{min(100, max(0, int(places)))}f}", value, precision)
    return visible, text


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


def get(value: object, key: str) -> object:
    """Read a field of a shared record, or None for a null record."""
    return None if value is None else cast(Mapping[str, object], value).get(key)
