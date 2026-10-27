"""Line-by-line emission of generated Python, the base layer of the lowering builder.

The ``lowering_*`` modules extend one another in order: writer, values, expressions, operations.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from ax_devil.modules.video_player.engine.drawing import Paint

from . import colormaps, emission, kernels
from .definitions import ValueType

MISSING = object()
TRUE, FALSE = "True", "False"

_HELPERS = (
    kernels.fire,
    kernels.relay,
    kernels.bad,
    kernels.failure,
    kernels.any_rows,
    kernels.invert,
    kernels.select,
    kernels.finite,
    kernels.minimum,
    kernels.maximum,
    kernels.divide,
    kernels.unit_scale,
    kernels.anchor_factors,
    kernels.nonempty,
    kernels.lengths,
    kernels.isin,
    kernels.per_row,
    emission.masked,
    emission.run_columns,
    emission.run_records,
    emission.collection_runs,
    emission.fill_columns,
    kernels.arrow,
    colormaps.ramp,
    colormaps.ramp_rows,
    colormaps.pick_color,
    colormaps.pick_color_rows,
    kernels.trim_text,
    kernels.number_text,
    kernels.percent_text,
    kernels.lookup_rows,
    kernels.get,
)


class CodeWriter:
    """Generated source lines, the globals they run against and the mask algebra over their names."""

    def __init__(self, table: bool, roots: Mapping[str, ValueType]) -> None:
        self.table = table
        self.roots = roots
        self.lines: list[str] = []
        self.level = 1
        self.count = 0
        self.initial: list[str] = []
        self.sink: str | None = None
        self.globals: dict[str, Any] = {
            "__builtins__": {},
            "missing": MISSING,
            "np": np,
            "Paint": Paint,
            "Exception": Exception,
            **{helper.__name__: helper for helper in _HELPERS},
        }

    def unique(self) -> str:
        self.count += 1
        return f"v{self.count}"

    def bind(self, value: Any) -> str:
        name = self.unique()
        self.globals[name] = value
        return name

    def line(self, code: str) -> None:
        self.lines.append(f"{'    ' * self.level}{code}")

    def assign(self, code: str) -> str:
        if code.isidentifier() or code in (TRUE, FALSE, "None"):
            return code
        name = self.unique()
        self.line(f"{name} = {code}")
        return name

    def both(self, first: str, second: str) -> str:
        if first == TRUE:
            return second
        if second == TRUE or first == second:
            return first
        if FALSE in (first, second):
            return FALSE
        return self.assign(f"({first}) & ({second})")

    def negate(self, mask: str) -> str:
        if mask in (TRUE, FALSE):
            return FALSE if mask == TRUE else TRUE
        return self.assign(f"invert({mask})")

    def pick(self, condition: str, then: str, other: str) -> str:
        if then == other:
            return then
        if condition == TRUE:
            return then
        if condition == FALSE:
            return other
        return self.assign(f"select({condition}, {then}, {other})")

    def require(
        self, ok: str, varying: bool, message: str, active: str, location: str, code: str = "invalid_value"
    ) -> None:
        if active == FALSE or ok == TRUE:
            return
        check = self.bind((code, location, message))
        if not varying:
            self.line(f"if not ({ok}):")
            self.level += 1
            self.report(active, check)
            self.level -= 1
            return
        rows = self.unique()
        self.line(f"{rows} = bad({ok}, {active})")
        self.line(f"if {rows} is not False:")
        self.level += 1
        self.report(rows, check)
        self.level -= 1

    def report(self, rows: str, check: str) -> None:
        if self.sink is None:
            self.line(f"fire(S, {check}, {rows})")
        else:
            self.line(f"{self.sink}.append(({rows}, {check}))")
