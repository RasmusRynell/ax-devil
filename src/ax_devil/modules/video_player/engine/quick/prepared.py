"""The data a prepared drawing is made of: final backend values for display and native-size export."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import NamedTuple

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor

from ..drawing import LabelSprite, _TextBlock
from ..render_context import RenderContext


@lru_cache(maxsize=1024)
def color(red: int, green: int, blue: int, alpha: int = 255) -> QColor:
    """Share immutable colors across prepared frames."""
    return QColor(red, green, blue, alpha)


@dataclass(frozen=True, slots=True)
class DrawingSettings:
    """Exact display/export geometry and backend inputs, separate from catalog units."""

    width: float
    height: float
    scale: float
    dpi: int = 96
    dpr: float = 1.0
    hardware: bool = False
    viewport: tuple[float, float, float, float] | None = None

    @classmethod
    def for_context(cls, context: RenderContext) -> DrawingSettings:
        """Construct uncropped software preparation settings for a logical target."""
        return cls(context.width, context.height, context.scale_factor)


class MeshData(NamedTuple):
    """Final screen-space vertex bytes for one geometry node."""

    payload: bytes
    operation_count: int = 1


class PathData(NamedTuple):
    """Resolved Qt Shape properties for geometry delegated to Qt's path renderer."""

    geometry: str
    position: QPointF
    stroke: QColor
    fill: QColor
    width: float
    dashed: bool


class LabelData(NamedTuple):
    """A painted label and its native top-left position, snapped to device pixels."""

    sprite: LabelSprite
    position: QPointF


class TextData(NamedTuple):
    """Shaped glyph layouts, color and native position; no text interpretation remains."""

    block: _TextBlock
    position: QPointF
    color: QColor


@dataclass(frozen=True, slots=True)
class PreparedDrawing:
    """Immutable submission data; no normalized drawing objects or replay program.

    Kinds are layers drawn bottom to top as meshes, paths, texts, then labels, so text stays readable.
    Order across entities is not preserved; within the mesh layer emission order is kept.
    """

    meshes: tuple[MeshData, ...] = ()
    paths: tuple[PathData, ...] = ()
    texts: tuple[TextData, ...] = ()
    labels: tuple[LabelData, ...] = ()
    counts: tuple[tuple[str, int], ...] = ()

    def __len__(self) -> int:
        operations = sum(mesh.operation_count for mesh in self.meshes)
        return operations + len(self.paths) + len(self.texts) + len(self.labels)


EMPTY_DRAWING = PreparedDrawing()
