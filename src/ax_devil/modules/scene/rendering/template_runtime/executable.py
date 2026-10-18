"""Lower validated catalog plans to row-column Python functions, once at load time.

A generated function evaluates one program for every row of a table at once. Each value is a
Python value shared by every row or a NumPy array with one entry per row. Laziness becomes row
masks: ``enabled``, ``if`` and ``coalesce`` narrow the active rows, and a failed check fails only
the active rows that demanded it. Failed rows emit nothing; the others are unaffected.

Only compiler-owned syntax is emitted. Catalog strings and objects are bound globals, never
executable source. Runtime executes array operations and branches, not plan nodes.

To learn this package, read ``definitions.py``, ``compiler.py``, ``plan.py``, then the lowering layers
in the order they extend one another (``lowering_writer.py``, ``lowering_values.py``,
``lowering_expressions.py``, ``lowering_operations.py``), this module and ``kernels.py``; the package README
describes what each module owns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, cast

import numpy as np

from . import emission
from .definitions import CONTEXT, ValueType
from .kernels import RowState
from .lowering_expressions import Scope
from .lowering_operations import OperationBuilder
from .lowering_values import Value
from .lowering_writer import FALSE, TRUE
from .plan import Node, Program


class _Builder(OperationBuilder):
    """Lowers a whole program, including style nodes, primitives and the final function."""

    def node_style(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        primitive: str = node.operand
        value = self.expression(node.children[0], scope, active, location)
        assert value.fields is not None
        varying = value.varying
        if primitive == "label":
            style = self.field(value, "label")
            assert style.fields is not None
            fields = dict(style.fields)
            for name in ("size", "padding_x", "padding_y", "radius", "gap"):
                amount = self.pixels(fields[name], active, location)
                comparison, rule = (">", "positive") if name == "size" else (">=", "nonnegative")
                self.require(
                    f"{amount.code} {comparison} 0", amount.varying, f"Label {name} must be {rule}.", active, location
                )
                fields[name] = Value(self.assign(f"{amount.code} / context.scale_factor"), varying=amount.varying)
            return Value(self.rows(replace(style, fields=fields)), varying=varying)
        if primitive == "text":
            style = self.field(value, "text")
            assert style.fields is not None
            color = self.field(style, "color")
            assert color.items is not None
            size = self.pixels(self.field(style, "size"), active, location)
            self.require(f"{size.code} > 0", size.varying, "Font size must be positive.", active, location)
            family = self.field(style, "family")
            name = self.pick(family.present, family.code, self.bind("Arial"))
            channels = ", ".join(item.code for item in color.items)
            return Value(
                self.assign(f"Paint({channels}, 0.0, False, 0, 0, 0, 0, {name}, {size.code} / context.scale_factor)"),
                varying=varying,
            )
        stroke, fill = self.field(value, "stroke"), self.field(value, "fill")
        pen = ("0", "0", "0")
        width, dashed = "0.0", FALSE
        if stroke.present != FALSE:
            drawn = self.both(active, stroke.present)
            color = self.field(stroke, "color")
            assert color.items is not None
            amount = self.pixels(self.field(stroke, "width"), drawn, location)
            scaled = self.assign(f"{amount.code} / context.scale_factor")
            self.require(f"{scaled} >= 0", amount.varying, "Stroke width must be nonnegative.", drawn, location)
            pen = tuple(self.pick(stroke.present, item.code, "0") for item in color.items)  # type: ignore[assignment]
            width = self.pick(stroke.present, scaled, "0.0")
            dashed = self.pick(stroke.present, self.assign(f"{self.field(stroke, 'pattern').code} == 'dash'"), FALSE)
        brush = ("0", "0", "0")
        alpha = "0"
        if fill.present != FALSE:
            color = self.field(fill, "color")
            assert color.items is not None
            brush = tuple(self.pick(fill.present, item.code, "0") for item in color.items)  # type: ignore[assignment]
            alpha = self.pick(fill.present, self.field(fill, "alpha").code, "0")
        radius = "0.0"
        corner = self.field(value, "radius")
        if corner.present != FALSE:
            rounded = self.both(active, corner.present)
            amount = self.pixels(corner, rounded, location)
            scaled = self.assign(f"{amount.code} / context.scale_factor")
            self.require(f"{scaled} >= 0", amount.varying, "Corner radius must be nonnegative.", rounded, location)
            radius = self.pick(corner.present, scaled, "0.0")
        return Value(
            self.assign(
                f"Paint({', '.join(pen)}, {width}, {dashed}, {', '.join(brush)}, {alpha}, 'Arial', 0.02, {radius})"
            ),
            varying=varying,
        )

    def program(self, program: Program, scope: Scope, active: str) -> None:
        enabled = self.expression(program.enabled, scope, active)
        rows = self.both(active, enabled.code)
        if rows == FALSE:
            return
        self.line(f"if any_rows({rows}):")
        self.level += 1
        self.line("pass")
        for step in program.steps:
            condition = self.expression(step.enabled, scope, rows)
            selected = self.both(rows, condition.code)
            if selected == FALSE:
                continue
            self.line(f"if any_rows({selected}):")
            self.level += 1
            self.line("pass")
            values = self.expression(step.fields, scope, selected)
            target = step.target
            if target is not None:
                self.program(
                    target, Scope(target.values, {"parameters": values, "context": scope.inputs["context"]}), selected
                )
            else:
                assert step.style is not None
                paint = self.expression(step.style, scope, selected, step.location)
                self.primitive(step.primitive, values, paint, selected, step.location)
            self.level -= 1
        self.level -= 1

    def primitive(self, kind: str, fields: Value, paint: Value, rows: str, location: str) -> None:
        assert fields.items is not None
        first = fields.items[0]
        if kind in ("polygon", "polyline"):
            if first.items is not None:
                points = ", ".join(
                    f"({self.field(item, 'x').code}, {self.field(item, 'y').code})" for item in first.items
                )
                argument = self.assign(
                    f"{self.bind(emission.PointRows)}(({points}{',' if first.items else ''}), {first.size or 'None'})"
                )
            else:
                argument = self.assign(f"{self.bind(emission.PointRows)}(collections={first.code})")
            self.add(f"{kind}s", rows, "ps", [argument, paint.code])
            return
        x, y = self.field(first, "x").code, self.field(first, "y").code
        if kind == "box":
            w, h = self.field(first, "w").code, self.field(first, "h").code
            # Zero-area boxes emit nothing.
            self.add("boxes", self.both(rows, self.assign(f"({w} > 0) & ({h} > 0)")), "nnnns", [x, y, w, h, paint.code])
        elif kind == "line":
            end = fields.items[1]
            self.add("lines", rows, "nnnns", [x, y, self.field(end, "x").code, self.field(end, "y").code, paint.code])
        elif kind == "point":
            self.add("points", rows, "nns", [x, y, paint.code])
        elif kind == "text":
            self.add("texts", rows, "nnvvs", [x, y, fields.items[1].code, fields.items[2].code, paint.code])
        elif kind == "label":
            # Keep shared labels shared when every row succeeded; inactive/failed rows never assemble content.
            active = self.both(rows, self.assign("~S.fail if S.hits else True"))
            content = self.assign(
                f"per_row({self.bind(emission.label_content)}, {paint.code}, {self.rows(fields.items[2])}, {active})"
            )
            self.add("labels", rows, "nnvv", [x, y, fields.items[1].code, content])
        elif kind == "circle":
            radius = self.pixels(fields.items[1], rows, location)
            self.require(f"{radius.code} >= 0", radius.varying, "Radius must be nonnegative.", rows, location)
            drawn = self.both(rows, self.assign(f"{radius.code} > 0"))
            self.add("circles", drawn, "nnns", [x, y, f"{radius.code} / context.scale_factor", paint.code])

    def add(self, kind: str, rows: str, spec: str, args: list[str]) -> None:
        self.line(f"S.pending.append(({kind!r}, {rows}, {spec!r}, ({', '.join(args)},)))")

    def finish(self, result: str = "S") -> Callable[..., Any]:
        header = ["def update(inputs, context, S):"]
        header.extend(f"    {line}" for line in self.initial)
        source = "\n".join((*header, *self.lines, f"    return {result}"))
        exec(compile(source, "<catalog update>", "exec"), self.globals)
        return cast(Callable[..., Any], self.globals.pop("update"))


def _context() -> Value:
    return Value(fields={name: Value(f"context.{name}") for name, _ in CONTEXT.fields})


def compile_rows(program: Program, roots: Mapping[str, ValueType], *, table: bool) -> Callable[..., Any]:
    """Compile a program for a table of rows, or for one row of shared mapping inputs.

    The function has the signature ``update(inputs, context, state)`` and records failures and
    primitive batches in the ``RowState``.
    """
    builder = _Builder(table, roots)
    builder.program(program, Scope(program.values, {"context": _context()}), TRUE)
    return builder.finish()


def evaluate(plan: Node, context: object) -> object:
    """Evaluate a compile-time constant through the generated shared-value semantics."""
    builder = _Builder(False, {})
    result = builder.materialize(builder.expression(plan, Scope((), {"context": _context()}), TRUE))
    state = RowState(1)
    with np.errstate(all="ignore"):
        value = builder.finish(result)({}, context, state)
    if state.hits:
        raise state.first_error()
    return value
