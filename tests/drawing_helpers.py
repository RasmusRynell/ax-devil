"""Test-only drawing call records for language assertions and pixel fixtures.

These records spy on the direct target protocol; production never creates or replays them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Protocol, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray

from ax_devil.modules.scene.model import Scene
from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalog
from ax_devil.modules.scene.rendering.template_runtime.definitions import ValueType, validate_value
from ax_devil.modules.scene.rendering.template_runtime.kernels import RowState, emit, objects_from
from ax_devil.modules.scene.rendering.template_runtime.program import RenderCatalog
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic, TemplateRuntimeError
from ax_devil.modules.video_player.engine.drawing import (
    LabelColumns,
    LabelWeight,
    Paint,
    Points,
    RunColumns,
)
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext

PenStyle: TypeAlias = Literal["solid", "dash"]


@dataclass(frozen=True, slots=True)
class DrawingStyle:
    """Immutable scalar style of one recorded primitive; lengths are relative to min(width,height)."""

    pen_r: int = 255
    pen_g: int = 0
    pen_b: int = 0
    pen_width: float = 0.002
    pen_style: PenStyle = "solid"
    brush_r: int = 0
    brush_g: int = 0
    brush_b: int = 0
    brush_a: int = 0  # 0 = no fill
    font_family: str = "Arial"
    font_size: float = 0.02
    radius: float = 0.0


_INTEGER_FIELDS = frozenset({"pen_r", "pen_g", "pen_b", "brush_r", "brush_g", "brush_b", "brush_a"})


def paint_of(style: DrawingStyle) -> Paint:
    """Share one style between every row of a batch."""
    return Paint(
        style.pen_r,
        style.pen_g,
        style.pen_b,
        style.pen_width,
        style.pen_style == "dash",
        style.brush_r,
        style.brush_g,
        style.brush_b,
        style.brush_a,
        style.font_family,
        style.font_size,
        style.radius,
    )


def _scalar(name: str, value: object) -> object:
    if name in _INTEGER_FIELDS:
        return int(cast(float, value))
    if name == "dashed":
        return bool(value)
    if name == "font_family":
        return str(value)
    return float(cast(float, value))


@lru_cache(maxsize=1024)
def _style(
    pen_r: int,
    pen_g: int,
    pen_b: int,
    pen_width: float,
    dashed: bool,
    brush_r: int,
    brush_g: int,
    brush_b: int,
    brush_a: int,
    font_family: str,
    font_size: float,
    radius: float,
) -> DrawingStyle:
    return DrawingStyle(
        pen_r,
        pen_g,
        pen_b,
        pen_width,
        "dash" if dashed else "solid",
        brush_r,
        brush_g,
        brush_b,
        brush_a,
        font_family,
        font_size,
        radius,
    )


def style_at(paint: Paint, row: int) -> DrawingStyle:
    """Return one row of a paint as an immutable scalar style."""
    values = []
    for name in Paint.__dataclass_fields__:
        value = getattr(paint, name)
        values.append(_scalar(name, value[row] if type(value) is np.ndarray else value))
    return _style(*values)


class DrawCall(Protocol):
    """A fixture that invokes the production drawing target."""

    def submit(self, target: DrawingBuffer) -> None:
        """Issue this fixture's drawing calls."""


DrawCalls = list[DrawCall]
Numbers = NDArray[np.float64]


@dataclass(frozen=True)
class BoxCall:
    """Recorded box instruction."""

    x: float
    y: float
    w: float
    h: float
    style: DrawingStyle = DrawingStyle()

    def submit(self, target: DrawingBuffer) -> None:
        """Invoke the real preparation target."""
        target.boxes(_row(self.x), _row(self.y), _row(self.w), _row(self.h), paint_of(self.style))


@dataclass(frozen=True)
class CircleCall:
    """Recorded circle instruction."""

    cx: float
    cy: float
    r: float
    style: DrawingStyle = DrawingStyle()

    def submit(self, target: DrawingBuffer) -> None:
        """Invoke the real preparation target."""
        target.circles(_row(self.cx), _row(self.cy), _row(self.r), paint_of(self.style))


@dataclass(frozen=True)
class TextCall:
    """Recorded text instruction."""

    x: float
    y: float
    text: str
    style: DrawingStyle = DrawingStyle()
    anchor: str = "baseline"

    def submit(self, target: DrawingBuffer) -> None:
        """Invoke the real preparation target."""
        target.texts(_row(self.x), _row(self.y), [self.text], [self.anchor], paint_of(self.style))


@dataclass(frozen=True, slots=True)
class LabelRun:
    """One run of a recorded label: text, or a bar that much filled."""

    text: str
    color: tuple[int, int, int]
    weight: LabelWeight = "regular"
    bar: float | None = None


@dataclass(frozen=True, slots=True)
class LabelContent:
    """One recorded label row of ``LabelColumns``; a row with nothing to show has no runs."""

    runs: tuple[LabelRun, ...]
    size: float
    family: str = ""
    background: tuple[int, int, int, int] | None = None
    padding_x: float = 0.0
    padding_y: float = 0.0
    radius: float = 0.0
    gap: float = 0.0


def _listed(value: object, count: int) -> list[Any]:
    return cast("list[Any]", value.tolist() if isinstance(value, np.ndarray) else [value] * count)


def label_contents(columns: LabelColumns, count: int) -> list[LabelContent]:
    """Each of *count* rows of *columns* as a ``LabelContent``."""
    shown = [
        [
            LabelRun(text, (red, green, blue), weight, None if bar != bar else bar)
            if present and (text or bar == bar)
            else None
            for text, bar, red, green, blue, weight, present in zip(
                *(_listed(getattr(run, name), count) for name in RunColumns.__dataclass_fields__)
            )
        ]
        for run in columns.runs
    ]
    fills: list[tuple[int, int, int, int] | None] = [None] * count
    if columns.background is not None:
        red, green, blue, alpha = (_listed(c, count) for c in columns.background)
        fills = [(r, g, b, a) if a else None for r, g, b, a in zip(red, green, blue, alpha)]
    sizes = zip(
        *(
            _listed(getattr(columns, name), count)
            for name in ("size", "family", "padding_x", "padding_y", "radius", "gap")
        )
    )
    return [
        LabelContent(tuple(run for run in runs if run is not None), size, family, fill, px, py, radius, gap)
        for runs, fill, (size, family, px, py, radius, gap) in zip(zip(*shown), fills, sizes)
    ]


@dataclass(frozen=True)
class LabelCall:
    """Recorded label instruction."""

    x: float
    y: float
    anchor: str
    content: LabelContent

    def submit(self, target: DrawingBuffer) -> None:
        """Invoke the real preparation target."""
        content = self.content
        runs = tuple(
            RunColumns(run.text, math.nan if run.bar is None else run.bar, *run.color, run.weight, True)
            for run in content.runs
        )
        columns = LabelColumns(
            runs or (RunColumns("", math.nan, 0, 0, 0, "regular", False),),
            content.size,
            content.family,
            content.background,
            content.padding_x,
            content.padding_y,
            content.radius,
            content.gap,
        )
        target.labels(_row(self.x), _row(self.y), self.anchor, columns)


@dataclass(frozen=True)
class LineCall:
    """Recorded line instruction."""

    x1: float
    y1: float
    x2: float
    y2: float
    style: DrawingStyle = DrawingStyle()

    def submit(self, target: DrawingBuffer) -> None:
        """Invoke the real preparation target."""
        target.lines(_row(self.x1), _row(self.y1), _row(self.x2), _row(self.y2), paint_of(self.style))


@dataclass(frozen=True)
class PointCall:
    """Recorded point instruction."""

    x: float
    y: float
    style: DrawingStyle = DrawingStyle()

    def submit(self, target: DrawingBuffer) -> None:
        """Invoke the real preparation target."""
        target.points(_row(self.x), _row(self.y), paint_of(self.style))


@dataclass(frozen=True)
class PolygonCall:
    """Recorded polygon instruction."""

    points: tuple[tuple[float, float], ...]
    style: DrawingStyle = DrawingStyle()

    def submit(self, target: DrawingBuffer) -> None:
        """Invoke the real preparation target."""
        target.polygons([self.points], paint_of(self.style))


@dataclass(frozen=True)
class PolylineCall:
    """Recorded polyline instruction."""

    points: tuple[tuple[float, float], ...]
    style: DrawingStyle = DrawingStyle()

    def submit(self, target: DrawingBuffer) -> None:
        """Invoke the real preparation target."""
        target.polylines([self.points], paint_of(self.style))


class RecordingTarget:
    """Capture each row of every batch as a call record, without involving Qt geometry or fonts."""

    def __init__(self) -> None:
        self.calls: DrawCalls = []

    def boxes(self, x: Numbers, y: Numbers, w: Numbers, h: Numbers, paint: Paint) -> None:
        """Record the resolved instruction arguments."""
        for row in range(len(x)):
            self.calls.append(BoxCall(float(x[row]), float(y[row]), float(w[row]), float(h[row]), style_at(paint, row)))

    def circles(self, x: Numbers, y: Numbers, radius: Numbers, paint: Paint) -> None:
        """Record the resolved instruction arguments."""
        for row in range(len(x)):
            self.calls.append(CircleCall(float(x[row]), float(y[row]), float(radius[row]), style_at(paint, row)))

    def texts(self, x: Numbers, y: Numbers, text: Sequence[str], anchor: Sequence[str], paint: Paint) -> None:
        """Record the resolved instruction arguments."""
        for row in range(len(x)):
            call = TextCall(float(x[row]), float(y[row]), str(text[row]), style_at(paint, row), str(anchor[row]))
            self.calls.append(call)

    def labels(self, x: Numbers, y: Numbers, anchor: object, content: LabelColumns) -> None:
        """Record the resolved instruction arguments."""
        anchors = anchor.tolist() if isinstance(anchor, np.ndarray) else [anchor] * len(x)
        for row, item in enumerate(label_contents(content, len(x))):
            self.calls.append(LabelCall(float(x[row]), float(y[row]), str(anchors[row]), item))

    def lines(self, x1: Numbers, y1: Numbers, x2: Numbers, y2: Numbers, paint: Paint) -> None:
        """Record the resolved instruction arguments."""
        for row in range(len(x1)):
            call = LineCall(float(x1[row]), float(y1[row]), float(x2[row]), float(y2[row]), style_at(paint, row))
            self.calls.append(call)

    def points(self, x: Numbers, y: Numbers, paint: Paint) -> None:
        """Record the resolved instruction arguments."""
        for row in range(len(x)):
            self.calls.append(PointCall(float(x[row]), float(y[row]), style_at(paint, row)))

    def polygons(self, points: Sequence[Points] | Numbers, paint: Paint) -> None:
        """Record the resolved instruction arguments."""
        self.calls.extend(PolygonCall(_pairs(shape), style_at(paint, row)) for row, shape in enumerate(points))

    def polylines(self, points: Sequence[Points] | Numbers, paint: Paint) -> None:
        """Record the resolved instruction arguments."""
        self.calls.extend(PolylineCall(_pairs(shape), style_at(paint, row)) for row, shape in enumerate(points))


CONTEXT = RenderContext.create(800, 400)
"""The render size most rendering tests draw at."""


def _pairs(shape: Sequence[tuple[float, float]] | Numbers) -> Points:
    return tuple((float(x), float(y)) for x, y in shape)


def _row(value: float) -> Numbers:
    return np.array([value], dtype=np.float64)


def record_template(
    catalog: RenderCatalog, name: str, inputs: Mapping[str, object], *, context: RenderContext, rows: bool = True
) -> DrawCalls:
    """Inspect the instructions emitted by a compiled template.

    Unless disabled, the same inputs also run as two identical rows through the per-row table code
    path, which must emit every call once per row.
    """
    target = RecordingTarget()
    try:
        catalog.render(name, inputs, target, context=context)
    except TemplateRuntimeError as error:
        if rows:
            state = _render_rows(catalog, name, inputs, context, RecordingTarget())
            assert state.fail.all(), "Table evaluation accepted a row that failed shared evaluation"
            assert (state.hits[0][0], state.hits[0][2]) == (error.diagnostic.code, error.diagnostic.message)
        raise
    if rows:
        table_target = RecordingTarget()
        _render_rows(catalog, name, inputs, context, table_target)
        assert table_target.calls == [call for call in target.calls for _ in range(_ROWS)]
    return target.calls


_ROWS = 2


class _MappingTable:
    """Identical rows of already validated mapping inputs, exposed through the table column contract."""

    def __init__(self, inputs: Mapping[str, object], roots: Mapping[str, ValueType]) -> None:
        self.count = _ROWS
        self._inputs = inputs
        self._roots = roots

    def column(self, path: tuple[str, ...]) -> tuple[object, tuple[object, ...]]:
        value: object = self._inputs.get(path[0])
        kind = self._roots[path[0]]
        for part in path[1:]:
            nullable = kind.nullable
            value = None if value is None else cast(Mapping[str, object], value).get(part)
            kind = kind.field(part).optional() if nullable else kind.field(part)
        return _column(value, kind), ()


def _column(value: object, kind: ValueType) -> tuple[object, object]:
    present = np.full(_ROWS, value is not None)
    if kind.fields:
        record = cast(Mapping[str, object], value) if value is not None else {}
        fields = {key: _column(record.get(key) if value is not None else None, child) for key, child in kind.fields}
        return present, {key: (present & column[0], column[1]) for key, column in fields.items()}
    if kind.name == "rgb":
        channels = cast(tuple[float, ...], value) if value is not None else (0, 0, 0)
        return present, tuple(np.full(_ROWS, float(channel)) for channel in channels)
    if kind.item is not None:
        return present, objects_from([value if value is not None else ()] * _ROWS)
    if kind.name in ("number", "integer"):
        return present, np.full(_ROWS, float(cast(float, value)) if value is not None else 0.0)
    if kind.name == "bool":
        return present, np.full(_ROWS, bool(value))
    return present, objects_from([value if value is not None else ""] * _ROWS)


def _render_rows(
    catalog: RenderCatalog, name: str, inputs: Mapping[str, object], context: RenderContext, target: RecordingTarget
) -> RowState:
    program = catalog.programs[name]
    parameters = validate_value({**program.parameter_defaults, **inputs}, program.parameter_type)
    state = RowState(_ROWS)
    with np.errstate(all="ignore"):
        program.compile_table()(_MappingTable({"parameters": parameters}, program.roots), context, state)
    emit(target, state)
    return state


def record_scene(
    catalog: SceneRenderCatalog,
    scene: Scene,
    context: RenderContext,
    *,
    diagnostics: list[CatalogDiagnostic] | None = None,
) -> DrawCalls:
    """Inspect scene language semantics independently of rendering."""
    target = RecordingTarget()
    catalog.render_scene(scene, context, target, diagnostics=diagnostics)
    return target.calls


def prepare_calls(calls: DrawCalls, target: DrawingBuffer) -> PreparedDrawing:
    """Issue pixel-test fixtures directly into the production preparation target."""
    for call in calls:
        call.submit(target)
    return target.finish()
