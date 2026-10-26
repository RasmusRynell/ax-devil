"""Vertex layout shared by overlay preparation and the overlay material, with the helpers every quad builder uses."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray


class OverlayKind(IntEnum):
    """Shape selector stored in the ``kind`` vertex field."""

    ROUNDED_RECT = 0
    ELLIPSE = 1
    LINE = 2
    GLYPH = 3
    FILL = 4


# Four vertices per quad in target logical px, implicit indices (0, 1, 2, 2, 1, 3) per quad.
# Kinds 0-2: u, v are the pixel position inside the shape (a line's u runs along its length) and w, h the shape size;
#   stroke is inward, dash is the period in px (0 = solid) and the pen colour is the stroke colour.
#   A line is drawn in fill; its radius is the cap radius (0 = square, half its height = round) and its stroke
#   field is the dash phase, the distance along the dashes at u = 0.
# Kind 3: u, v are normalized coordinates in the atlas page and fill tints the glyph coverage.
# Kind 4: the quad is the interpolated fill colour of its corners. A triangle repeats its third corner as the fourth.
# Colours are premultiplied RGBA.
OVERLAY_VERTEX = np.dtype(
    [
        ("x", "f4"),
        ("y", "f4"),
        ("u", "f4"),
        ("v", "f4"),
        ("w", "f4"),
        ("h", "f4"),
        ("radius", "f4"),
        ("stroke", "f4"),
        ("dash", "f4"),
        ("kind", "f4"),
        ("fill", "u1", (4,)),
        ("pen", "u1", (4,)),
    ]
)
assert OVERLAY_VERTEX.itemsize == 48

PAD = 1.0
"""Shape quads extend one pixel beyond the shape so the shader can antialias its edge."""
CORNER_U = np.array([0.0, 1.0, 0.0, 1.0])
CORNER_V = np.array([0.0, 0.0, 1.0, 1.0])
"""The corner order of a quad, which the implicit indices (0, 1, 2, 2, 1, 3) turn into two triangles."""


def per_row(value: object, count: int, dtype: Any = np.float64) -> NDArray[Any]:
    """One value per row of a shared or per-row column."""
    return np.broadcast_to(np.asarray(value, dtype=dtype), (count,))


def premultiplied(rgb: NDArray[Any], alpha: NDArray[Any] | int) -> NDArray[np.uint8]:
    """Premultiplied RGBA bytes of straight 0-255 colour rows."""
    alpha = np.broadcast_to(np.asarray(alpha, dtype=np.float64), (len(rgb),))
    rgba = np.column_stack((np.rint(rgb * alpha[:, None] / 255), alpha))
    clipped: NDArray[np.uint8] = np.clip(rgba, 0, 255).astype(np.uint8)
    return clipped
