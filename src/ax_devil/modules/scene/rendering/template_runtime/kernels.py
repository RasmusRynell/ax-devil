"""Row-column runtime for generated catalog functions.

A value is either one Python value shared by every row or a NumPy array with one entry per row.
Helpers are total: inactive and null rows hold placeholders and never raise here. Generated code
checks results per row and reports failures through ``fire``; failed rows emit nothing.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache, reduce
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

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


def get(value: object, key: str) -> object:
    """Read a field of a shared record, or None for a null record."""
    return None if value is None else cast(Mapping[str, object], value).get(key)
