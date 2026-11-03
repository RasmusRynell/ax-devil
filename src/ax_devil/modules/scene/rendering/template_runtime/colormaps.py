"""Colormap kernels for generated catalog functions: stop ramps and palette picks, per row."""

from __future__ import annotations

import math
import zlib
from functools import lru_cache
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from .kernels import per_row
from .values import record, sequence


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
