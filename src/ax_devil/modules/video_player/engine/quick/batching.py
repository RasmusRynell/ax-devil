"""Pack ordered vertex batches with bulk NumPy transforms and reusable scratch storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from .geometry import VERTEX, RectangleTemplate

_LIMIT = 60_000


class VertexBatch(Protocol):
    """Primitives sharing one vertex count, written straight into packed vertex storage."""

    @property
    def rows(self) -> int:
        """Return the number of primitives."""

    @property
    def size(self) -> int:
        """Return the number of vertices per primitive."""

    def write(self, target: NDArray[np.void]) -> None:
        """Fill a (rows, size) vertex array."""


@dataclass(frozen=True, slots=True)
class RectangleBatch:
    """Rectangles sharing one topology, with per-row size, origin, rotation and premultiplied colors.

    Colors are (rows, 4) arrays or one (4,) color shared by every row. Angles are degrees, or None.
    """

    template: RectangleTemplate
    width: NDArray[np.float64]
    height: NDArray[np.float64]
    x: NDArray[np.float64]
    y: NDArray[np.float64]
    angle: NDArray[np.float64] | None
    fill: NDArray[np.float64]
    pen: NDArray[np.float64]

    @property
    def rows(self) -> int:
        """Return the number of rectangles."""
        return len(self.width)

    @property
    def size(self) -> int:
        """Return the number of vertices per rectangle."""
        return self.template.vertex_count

    def write(self, target: NDArray[np.void]) -> None:
        """Stretch the shared topology to each size, then place, rotate and color it."""
        template = self.template
        tx, ty, size = template.xy[:, 0], template.xy[:, 1], template.size
        x = tx + (self.width[:, None] - size).astype(np.float32) * (tx > size / 2)
        y = ty + (self.height[:, None] - size).astype(np.float32) * (ty > size / 2)
        left, top = self.x[:, None].astype(np.float32), self.y[:, None].astype(np.float32)
        if self.angle is None:
            target["x"] = x + left
            target["y"] = y + top
        else:
            angle = np.deg2rad(self.angle[:, None])
            cosine, sine = np.cos(angle).astype(np.float32), np.sin(angle).astype(np.float32)
            target["x"] = x * cosine - y * sine + left
            target["y"] = x * sine + y * cosine + top
        target["color"] = template.shading.colors(self.fill, self.pen)


class GeometryPacker:
    """Retain reusable vertex storage; publish immutable bytes safe for Qt consumption."""

    def __init__(self) -> None:
        self._vertices: NDArray[np.void] = np.empty(0, dtype=VERTEX)

    def pack(self, batches: list[VertexBatch]) -> tuple[tuple[bytes, int], ...]:
        """Preserve emission order and split only at the vertex capacity bound."""
        total = sum(batch.rows * batch.size for batch in batches)
        if not total:
            return ()
        if len(self._vertices) < total:
            self._vertices = np.empty(max(total, len(self._vertices) * 2), dtype=VERTEX)
        chunks: list[tuple[int, int, int]] = []
        start = count = operations = offset = 0
        for batch in batches:
            rows, size = batch.rows, batch.size
            batch.write(self._vertices[offset : offset + rows * size].reshape(rows, size))
            offset += rows * size
            remaining = rows
            while remaining:
                fit = min(remaining, (_LIMIT - count) // size)
                if not fit:
                    chunks.append((start, start + count, operations))
                    start, count, operations = start + count, 0, 0
                    continue
                count += fit * size
                operations += fit
                remaining -= fit
        chunks.append((start, start + count, operations))
        payload = self._vertices[:total].tobytes()
        return tuple(
            (payload[first * VERTEX.itemsize : last * VERTEX.itemsize], operations)
            for first, last, operations in chunks
        )
