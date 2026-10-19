"""Per-row array arguments of queued drawing batches: broadcasting, premultiplied colors and joining."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import numpy as np
from numpy.typing import NDArray

from .geometry import premultiplied


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
