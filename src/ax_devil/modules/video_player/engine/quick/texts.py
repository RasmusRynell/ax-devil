"""Shaped glyph layouts and positions for queued text rows, for display and export."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF

from ..drawing import _ANCHORS, Paint, _text_block, text_ink_bounds
from .columns import _floats
from .prepared import DrawingSettings, TextData, color


def prepare_texts(
    settings: DrawingSettings,
    area_visible: Callable[[float, float, float, float], bool],
    rows_visible: Callable[
        [NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]],
        NDArray[np.bool_],
    ],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    text: Sequence[str],
    anchor: Sequence[str],
    paint: Paint,
) -> list[TextData]:
    """Shape once and prepare final glyph layouts and positions for the visible rows, preserving overhangs."""
    s = settings
    count = len(x)
    texts: list[TextData] = []
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
    visible = rows_visible(left, top, widths, heights, np.zeros(count)).tolist()
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
            if not area_visible(ink.x() + position.x(), ink.y() + position.y(), ink.width(), ink.height()):
                continue
        texts.append(TextData(blocks[row], position, colors[row]))
    return texts
