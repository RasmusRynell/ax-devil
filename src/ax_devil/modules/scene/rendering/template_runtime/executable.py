"""Lower validated catalog plans to row-column Python functions, once at load time.

A generated function evaluates one program for every row of a table at once. Each value is a
Python value shared by every row or a NumPy array with one entry per row. Laziness becomes row
masks: ``enabled``, ``if`` and ``coalesce`` narrow the active rows, and a failed check fails only
the active rows that demanded it. Failed rows emit nothing; the others are unaffected.

Only compiler-owned syntax is emitted. Catalog strings and objects are bound globals, never
executable source. Runtime executes array operations and branches, not plan nodes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, cast

import numpy as np

from ax_devil.modules.video_player.engine.drawing import Paint

from . import kernels
from .definitions import (
    CONTEXT,
    RULES,
    ValueType,
    allowed_values,
    check_constraints,
    compile_validator,
    field_label,
    within_bounds,
)
from .kernels import ANCHORS, RowState

MISSING = object()
_NUMBERS = ("number", "integer")
_TRUE, _FALSE = "True", "False"


@dataclass(frozen=True, slots=True, eq=False)
class Node:
    """Temporary compiler instruction; no instruction dispatch occurs during rendering."""

    kind: str
    operand: Any = None
    children: tuple[Node, ...] = ()
    location: str = "$"


@dataclass(frozen=True, slots=True)
class Step:
    """Resolved guarded template call or primitive emission."""

    enabled: Node
    fields: Node
    style: Node | None
    target: Program | None
    primitive: str
    location: str


@dataclass(frozen=True, slots=True)
class Program:
    """Compile-time template structure, inlined before executable code is generated."""

    enabled: Node
    values: tuple[Node, ...]
    steps: tuple[Step, ...]


@dataclass(slots=True)
class _Value:
    """Compile-time description of a value: a leaf expression, record fields or list items.

    ``present`` is the null mask expression; ``varying`` means some part differs between rows.
    ``size`` is a per-row item count for lists whose trailing items may be absent.
    """

    code: str = "None"
    fields: dict[str, _Value] | None = None
    items: tuple[_Value, ...] | None = None
    present: str = _TRUE
    varying: bool = False
    const: object = MISSING
    size: str | None = None


@dataclass(slots=True)
class _Slot:
    flag: str
    value: _Value
    terms: str


@dataclass(slots=True)
class _Scope:
    values: tuple[Node, ...]
    inputs: dict[str, _Value]
    slots: dict[int, _Slot] = field(default_factory=dict)


def _placeholder(kind: ValueType) -> object:
    if kind.name in _NUMBERS:
        return 0
    if kind.name == "bool":
        return False
    if kind.name == "string" or kind.choices:
        return ""
    if kind.item is not None:
        return ()
    return None


class _Builder:
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
            **{name: getattr(kernels, name) for name in _HELPERS},
        }

    # Emission primitives -------------------------------------------------

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
        if code.isidentifier() or code in (_TRUE, _FALSE, "None"):
            return code
        name = self.unique()
        self.line(f"{name} = {code}")
        return name

    def both(self, first: str, second: str) -> str:
        if first == _TRUE:
            return second
        if second == _TRUE or first == second:
            return first
        if _FALSE in (first, second):
            return _FALSE
        return self.assign(f"({first}) & ({second})")

    def negate(self, mask: str) -> str:
        if mask in (_TRUE, _FALSE):
            return _FALSE if mask == _TRUE else _TRUE
        return self.assign(f"invert({mask})")

    def pick(self, condition: str, then: str, other: str) -> str:
        if then == other:
            return then
        if condition == _TRUE:
            return then
        if condition == _FALSE:
            return other
        return self.assign(f"select({condition}, {then}, {other})")

    def require(
        self, ok: str, varying: bool, message: str, active: str, location: str, code: str = "invalid_value"
    ) -> None:
        if active == _FALSE or ok == _TRUE:
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

    def finite(self, code: str, varying: bool, active: str, location: str) -> _Value:
        result = self.assign(code)
        self.require(f"finite({result})", varying, "Numeric result must be finite.", active, location)
        return _Value(result, varying=varying)

    def boolean(self, code: str, varying: bool) -> _Value:
        if code in (_TRUE, _FALSE):
            return _Value(code, const=code == _TRUE)
        return _Value(self.assign(code), varying=varying)

    # Values -----------------------------------------------------------------

    def constant(self, value: object) -> _Value:
        if value is None:
            return _Value("None", present=_FALSE, const=None)
        if isinstance(value, Mapping):
            fields = {str(key): self.constant(item) for key, item in value.items()}
            return _Value(fields=fields, const=value)
        if isinstance(value, (tuple, list)):
            return _Value(items=tuple(self.constant(item) for item in value), const=value)
        if isinstance(value, bool):
            return _Value(repr(value), const=value)
        return _Value(self.bind(value), const=value)

    def placeholder(self, kind: ValueType) -> _Value:
        if kind.fields:
            return _Value(
                fields={key: self.placeholder(child) for key, child in kind.fields}, present=_FALSE, const=None
            )
        if kind.name == "rgb":
            return _Value(items=tuple(_Value("0") for _ in range(3)), present=_FALSE, const=None)
        value = _placeholder(kind)
        return _Value("None" if value is None else self.bind(value), present=_FALSE, const=None)

    def conform(self, value: _Value, kind: ValueType | None) -> _Value:
        """Give a value the structure of its type, with typed placeholders for null parts."""
        if kind is None or kind.name in ("any", "null"):
            return value
        if kind.fields:
            if value.fields is None:
                if value.present == _FALSE:
                    return self.placeholder(kind)
                return self.present_in(self.destructure(value.code, kind, True), value.present)
            fields = {
                key: self.conform(value.fields.get(key, _Value("None", present=_FALSE, const=None)), child)
                for key, child in kind.fields
            }
            return replace(value, fields=fields, varying=value.varying or any(item.varying for item in fields.values()))
        if kind.name == "rgb" and value.items is None:
            if value.present == _FALSE:
                return self.placeholder(kind)
            return self.present_in(self.destructure(value.code, kind, True), value.present)
        if kind.item is None and value.fields is None and value.items is None and value.code == "None":
            placeholder = _placeholder(kind)
            if placeholder is not None:
                return replace(value, code=self.bind(placeholder))
        return value

    def present_in(self, value: _Value, present: str) -> _Value:
        return replace(value, present=self.both(present, value.present))

    def destructure(self, code: str, kind: ValueType, nullable: bool | None = None) -> _Value:
        """Describe a shared canonical value (mappings and tuples) by its type; null parts get placeholders."""
        value = self.assign(code)
        if nullable is None:
            nullable = kind.nullable or kind.name == "any"
        exists = self.assign(f"{value} is not None") if nullable else _TRUE
        if kind.fields:
            fields = {
                key: self.destructure(f"get({value}, {self.bind(key)})", child, nullable or None)
                for key, child in kind.fields
            }
            return _Value(fields=fields, present=exists)
        if kind.name == "rgb":
            items = tuple(_Value(self.assign(f"0 if {value} is None else {value}[{index}]")) for index in range(3))
            return _Value(items=items, present=exists)
        placeholder = _placeholder(kind)
        if nullable and placeholder is not None:
            value = self.assign(f"{self.bind(placeholder)} if {value} is None else {value}")
        return _Value(value, present=exists)

    def columns(self, code: str, kind: ValueType) -> _Value:
        """Describe a table column ``(present, data)`` by its type."""
        column = self.assign(code)
        present = self.assign(f"{column}[0]")
        data = f"{column}[1]"
        if kind.fields:
            fields = {key: self.columns(f"{data}[{self.bind(key)}]", child) for key, child in kind.fields}
            return _Value(fields=fields, present=present, varying=True)
        if kind.name == "rgb":
            items = tuple(_Value(self.assign(f"{data}[{index}]"), varying=True) for index in range(3))
            return _Value(items=items, present=present, varying=True)
        return _Value(self.assign(data), present=present, varying=True)

    def materialize(self, value: _Value) -> str:
        """Build the canonical Python value of a shared value."""
        if value.fields is not None:
            inner = (
                f"{{{', '.join(f'{self.bind(key)}: {self.materialize(item)}' for key, item in value.fields.items())}}}"
            )
        elif value.items is not None:
            inner = f"({', '.join(self.materialize(item) for item in value.items)}{',' if value.items else ''})"
            if value.size is not None:
                inner = f"{inner}[:{value.size}]"
        else:
            inner = value.code
        return inner if value.present == _TRUE else f"({inner} if {value.present} else None)"

    def rows(self, value: _Value) -> str:
        """Build per-row canonical values of a varying value (present parts only)."""
        if not value.varying:
            return self.materialize(value)
        leaves: list[str] = []

        def leaf(code: str) -> int:
            leaves.append(code)
            return len(leaves) - 1

        def shape(item: _Value) -> tuple[Any, ...]:
            if item.fields is not None:
                node: tuple[Any, ...] = ("fields", tuple((key, shape(child)) for key, child in item.fields.items()))
            elif item.items is not None:
                size = leaf(item.size) if item.size is not None else None
                node = ("items", tuple(shape(child) for child in item.items), size)
            else:
                node = ("leaf", leaf(item.code))
            return (*node, leaf(item.present) if item.present != _TRUE else None)

        structure = shape(value)
        return self.assign(f"per_row({self.bind(_rebuild(structure))}, {', '.join(leaves)})")

    def store(self, value: _Value) -> _Value:
        """Copy every part of a value into locals, so later code can reassign them."""
        if value.fields is not None:
            fields = {key: self.store(item) for key, item in value.fields.items()}
            return _Value(fields=fields, present=self.local(value.present), varying=value.varying, const=value.const)
        if value.items is not None:
            items = tuple(self.store(item) for item in value.items)
            size = self.local(value.size) if value.size is not None else None
            return _Value(
                items=items, present=self.local(value.present), varying=value.varying, const=value.const, size=size
            )
        return _Value(
            self.local(value.code), present=self.local(value.present), varying=value.varying, const=value.const
        )

    def local(self, code: str) -> str:
        name = self.unique()
        self.line(f"{name} = {code}")
        return name

    def copy(self, target: _Value, source: _Value) -> None:
        """Assign another branch's value to stored locals; flags describe either branch."""
        target.varying = target.varying or source.varying
        if target.const is not MISSING and (source.const is MISSING or source.const != target.const):
            target.const = MISSING
        self.line(f"{target.present} = {source.present}")
        if target.fields is not None:
            assert source.fields is not None
            for key, item in target.fields.items():
                self.copy(item, source.fields[key])
        elif target.items is not None:
            assert source.items is not None and len(source.items) == len(target.items)
            for item, other in zip(target.items, source.items):
                self.copy(item, other)
            if target.size is not None:
                self.line(f"{target.size} = {source.size if source.size is not None else len(source.items)}")
        else:
            self.line(f"{target.code} = {source.code}")

    def same_shape(self, first: _Value, second: _Value) -> bool:
        if (first.fields is None) != (second.fields is None) or (first.items is None) != (second.items is None):
            return False
        if first.fields is not None and second.fields is not None:
            return first.fields.keys() == second.fields.keys() and all(
                self.same_shape(item, second.fields[key]) for key, item in first.fields.items()
            )
        if first.items is not None and second.items is not None:
            return len(first.items) == len(second.items) and all(
                self.same_shape(a, b) for a, b in zip(first.items, second.items)
            )
        return True

    def normalize(self, value: _Value, kind: ValueType | None) -> _Value:
        """Represent collections of branch values as per-row canonical values, so branches share a structure."""
        if kind is None:
            return value
        if kind.fields and value.fields is not None:
            fields = {
                key: self.normalize(value.fields[key], child) for key, child in kind.fields if key in value.fields
            }
            return replace(value, fields=fields)
        if kind.item is not None and kind.name != "rgb" and value.items is not None:
            complete = replace(value, present=_TRUE)
            code = self.rows(complete) if value.varying else self.materialize(complete)
            return _Value(self.assign(code), present=value.present, varying=value.varying)
        return value

    def merge(self, condition: str, then: _Value, other: _Value, kind: ValueType | None) -> _Value:
        """Choose per row between two values of one type."""
        then, other = self.normalize(then, kind), self.normalize(other, kind)
        assert self.same_shape(then, other)
        present = self.pick(condition, then.present, other.present)
        if then.fields is not None and other.fields is not None:
            fields = {
                key: self.merge(condition, item, other.fields[key], kind.field(key) if kind and kind.fields else None)
                for key, item in then.fields.items()
            }
            return _Value(fields=fields, present=present, varying=True)
        if then.items is not None and other.items is not None:
            items = tuple(
                self.merge(condition, a, b, kind.item if kind else None) for a, b in zip(then.items, other.items)
            )
            size = None
            if then.size is not None or other.size is not None:
                size = self.pick(
                    condition,
                    then.size if then.size is not None else str(len(then.items)),
                    other.size if other.size is not None else str(len(other.items)),
                )
            return _Value(items=items, present=present, varying=True, size=size)
        if then.present == _FALSE:
            code = other.code
        elif other.present == _FALSE:
            code = then.code
        else:
            code = self.pick(condition, then.code, other.code)
        return _Value(code, present=present, varying=True)

    def field(self, value: _Value, key: str) -> _Value:
        if value.fields is None:
            return _Value(self.assign(f"get({self.materialize(value)}, {self.bind(key)})"), present=_TRUE)
        item = value.fields[key]
        return replace(item, present=self.both(value.present, item.present), varying=item.varying or value.varying)

    # Expressions ------------------------------------------------------------

    def external(self, node: Node, scope: _Scope) -> tuple[str, ...] | None:
        """Return the full path of a reference to program inputs outside the current scope."""
        tail: tuple[str, ...] = ()
        while node.kind in ("located", "access"):
            if node.kind == "access":
                tail = (*node.operand, *tail)
            node = node.children[0]
        if node.kind != "input" or node.operand[0] in scope.inputs:
            return None
        return (*node.operand, *tail)

    def kind_of(self, path: tuple[str, ...]) -> ValueType:
        kind = self.roots[path[0]].field(path[1])
        for part in path[2:]:
            nullable = kind.nullable
            kind = kind.field(part)
            if nullable:
                kind = kind.optional()
        return kind

    def reference(self, path: tuple[str, ...], active: str, location: str) -> _Value:
        kind = self.kind_of(path)
        if not self.table:
            return self.destructure(self.shared_reference(path), kind, True)
        column = self.unique()
        self.line(f"{column} = inputs.column({self.bind(path)})")
        self.line(f"relay(S, {self.sink or 'None'}, {column}[1], {active}, {location!r})")
        return self.columns(f"{column}[0]", kind)

    def shared_reference(self, path: tuple[str, ...]) -> str:
        code = f"get(inputs[{self.bind(path[0])}], {self.bind(path[1])})"
        for part in path[2:]:
            code = f"get({code}, {self.bind(part)})"
        return code

    def expression(
        self, node: Node, scope: _Scope, active: str, location: str = "$", kind: ValueType | None = None
    ) -> _Value:
        if node.kind == "located":
            location = node.location if node.location != "$" else location
            return self.conform(self.expression(node.children[0], scope, active, location, node.operand), node.operand)
        path = self.external(node, scope)
        if path is not None:
            return self.reference(path, active, location)
        handler = getattr(self, f"node_{node.kind}", None)
        if handler is None:
            raise ValueError(f"Unknown compiler instruction: {node.kind}")
        return cast(_Value, handler(node, scope, active, location, kind))

    def node_constant(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        return self.constant(node.operand)

    def node_slot(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        slot = scope.slots.get(node.operand)
        saved = self.sink
        if slot is None:
            flag, terms = self.unique(), self.unique()
            self.initial.append(f"{flag} = missing")
            self.initial.append(f"{terms} = ()")
        else:
            flag, terms = slot.flag, slot.terms
        self.line(f"if {flag} is missing:")
        self.level += 1
        self.line(f"{terms} = []")
        self.sink = terms
        # Memoized values are computed for every row; failures are reported where rows demand them.
        value = self.expression(scope.values[node.operand], scope, _TRUE, "$")
        self.sink = saved
        if slot is None:
            slot = _Slot(flag, self.store(value), terms)
            scope.slots[node.operand] = slot
        else:
            self.copy(slot.value, value)
        self.line(f"{flag} = None")
        self.level -= 1
        self.line(f"relay(S, {self.sink or 'None'}, {terms}, {active}, {location!r})")
        return slot.value

    def node_input(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        value = scope.inputs[node.operand[0]]
        for key in node.operand[1:]:
            value = self.field(value, key)
        return value

    def node_access(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        value = self.expression(node.children[0], scope, active, location)
        for key in node.operand:
            value = self.field(value, key)
        return value

    def node_record(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        children = [self.expression(child, scope, active, location) for child in node.children]
        return _Value(fields=dict(zip(node.operand, children)), varying=any(child.varying for child in children))

    def node_tuple(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        children = tuple(self.expression(child, scope, active, location) for child in node.children)
        return _Value(items=children, varying=any(child.varying for child in children))

    def node_fields(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        value = self.expression(node.children[0], scope, active, location)
        items = tuple(self.field(value, key) for key in node.operand)
        return _Value(items=items, varying=value.varying)

    def node_if(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        condition = self.expression(node.children[0], scope, active, location)
        if not condition.varying:
            if condition.const is True or condition.const is False:
                chosen = node.children[1 if condition.const else 2]
                return self.conform(self.expression(chosen, scope, active, location, kind), kind)
            # Shared conditions stay lazy: only the chosen branch executes.
            self.line(f"if {condition.code}:")
            self.level += 1
            then = self.normalize(
                self.conform(self.expression(node.children[1], scope, active, location, kind), kind), kind
            )
            target = self.store(then)
            self.level -= 1
            self.line("else:")
            self.level += 1
            other = self.normalize(
                self.conform(self.expression(node.children[2], scope, active, location, kind), kind), kind
            )
            assert self.same_shape(target, other)
            self.copy(target, other)
            self.level -= 1
            return target
        other_rows = self.negate(condition.code)
        then = self.conform(
            self.expression(node.children[1], scope, self.both(active, condition.code), location, kind), kind
        )
        other = self.conform(
            self.expression(node.children[2], scope, self.both(active, other_rows), location, kind), kind
        )
        return self.merge(condition.code, then, other, kind)

    def node_coalesce(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        result = self.conform(self.expression(node.children[0], scope, active, location, kind), kind)
        for child in node.children[1:]:
            if result.present == _TRUE:
                break
            if not result.varying:
                target = self.store(self.normalize(result, kind))
                self.line(f"if not ({target.present}):")
                self.level += 1
                value = self.normalize(self.conform(self.expression(child, scope, active, location, kind), kind), kind)
                assert self.same_shape(target, value)
                self.copy(target, value)
                self.level -= 1
                result = target
                continue
            missing = self.negate(result.present)
            value = self.conform(self.expression(child, scope, self.both(active, missing), location, kind), kind)
            result = self.merge(missing, value, result, kind)
        return result

    def node_validate(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        expected: ValueType = node.operand
        path = self.external(node.children[0], scope)
        if not self.table and path is not None:
            checked = self.unique()
            self.line("try:")
            self.level += 1
            self.line(f"{checked} = {self.bind(compile_validator(expected))}({self.shared_reference(path)})")
            self.level -= 1
            self.line("except Exception as error:")
            self.level += 1
            self.line(f"{checked} = None")
            self.report(active, f"failure(error, {location!r})")
            self.level -= 1
            return self.destructure(checked, expected, True)
        value = self.expression(node.children[0], scope, active, location, expected)
        if not expected.nullable:
            self.require(value.present, value.varying, f"Expected {expected.name}, got null.", active, location)
            value = replace(value, present=_TRUE)
        return value

    def node_constraint(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        value = self.expression(node.children[0], scope, active, location)
        self.constraint(value, node.operand, active, location)
        return value

    def constraint(self, value: _Value, kind: ValueType, active: str, location: str) -> None:
        rows = self.both(active, value.present)
        if kind.is_scalar and kind.bounds:
            self.bounds(value, kind, kind.name, rows, location)
            return
        if value.fields is None and value.items is None:
            if not value.varying:
                self.checked_in_python(value, kind, rows, location)
            return
        varying = value.varying
        if value.fields is not None:
            for key, field_kind in kind.fields:
                if field_kind.is_scalar and field_kind.bounds:
                    field = value.fields[key]
                    present = self.both(rows, field.present) if field_kind.nullable else rows
                    self.bounds(field, field_kind, field_label(kind, key, kind.name), present, location)
        if value.items is not None:
            if value.size is None and kind.bounds and not within_bounds(value.items, kind):
                self.require(_FALSE, False, f"{kind.name} must be {allowed_values(kind)}.", rows, location)
                return
            if value.size is not None and kind.bounds:
                self.bounds(_Value(value.size, varying=varying), kind, kind.name, rows, location)
            if kind.item is not None and kind.item.is_scalar and kind.item.bounds:
                for item in value.items:
                    self.bounds(item, kind.item, f"{kind.name} item", rows, location)
        if kind.name in ("polygon_fields", "polyline_fields") and value.fields is not None:
            count = self.count_items(value.fields["points"])
            minimum = 3 if kind.name == "polygon_fields" else 2
            self.require(
                f"({count} == 0) | ({count} >= {minimum})",
                varying,
                f"Geometry requires at least {minimum} points.",
                rows,
                location,
            )
        elif kind.name in RULES and not varying:
            self.checked_in_python(value, kind, rows, location)

    def bounds(self, value: _Value, kind: ValueType, label: str, rows: str, location: str) -> None:
        """Require a scalar, or an item count described by ``min_items``/``max_items``, to be within bounds."""
        code = value.code
        if kind.max_length is not None:
            code = self.assign(f"lengths({code})")
        limits = (
            (kind.minimum, ">="),
            (kind.exclusive_minimum, ">"),
            (kind.maximum, "<="),
            (kind.max_length, "<="),
            (kind.min_items, ">="),
            (kind.max_items, "<="),
        )
        ok = " & ".join(f"({code} {symbol} {limit!r})" for limit, symbol in limits if limit is not None)
        self.require(ok, value.varying, f"{label} must be {allowed_values(kind)}.", rows, location)

    def checked_in_python(self, value: _Value, kind: ValueType, rows: str, location: str) -> None:
        self.line("try:")
        self.level += 1
        self.line(f"{self.bind(check_constraints)}({self.materialize(value)}, {self.bind(kind)})")
        self.level -= 1
        self.line("except Exception as error:")
        self.level += 1
        self.report(rows, f"failure(error, {location!r})")
        self.level -= 1

    def count_items(self, value: _Value) -> str:
        if value.items is not None:
            return value.size if value.size is not None else str(len(value.items))
        return self.assign(f"lengths({value.code})")

    def node_convert(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        value = self.expression(node.children[0], scope, active, location)
        return self.convert(value, node.operand, active, location)

    def convert(self, value: _Value, descriptor: tuple[Any, ...], active: str, location: str) -> _Value:
        mode, kind, transforms, missing, child, spec = descriptor
        rows = self.both(active, value.present)
        if mode == 0 and value.fields is None and value.items is None:
            self.require(
                f"isin({value.code}, {self.bind(spec.choices)})",
                value.varying,
                f"Expected {spec.name}: {spec.choices or 'text'}.",
                rows,
                location,
            )
            return value
        if mode == 1 and value.fields is not None:
            fields = dict(value.fields)
            for key, conversion in transforms:
                fields[key] = self.convert(fields[key], conversion, active, location)
            for key in missing:
                fields[key] = _Value("None", present=_FALSE, const=None)
            result = replace(value, fields=fields)
            self.constraint(result, kind, active, location)
            return result
        if mode == 2 and value.items is not None:
            items = tuple(self.convert(item, child, active, location) if child else item for item in value.items)
            result = replace(value, items=items)
            self.constraint(result, kind, active, location)
            return result
        converter = self.bind(_converter(descriptor))
        if value.varying:
            return _Value(self.assign(f"per_row({converter}, {self.rows(value)})"), present=value.present, varying=True)
        converted = self.unique()
        self.line("try:")
        self.level += 1
        self.line(f"{converted} = {converter}({self.materialize(value)})")
        self.level -= 1
        self.line("except Exception as error:")
        self.level += 1
        self.line(f"{converted} = None")
        self.report(rows, f"failure(error, {location!r})")
        self.level -= 1
        return _Value(converted, present=value.present)

    def node_style(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
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
                fields[name] = _Value(self.assign(f"{amount.code} / context.scale_factor"), varying=amount.varying)
            return _Value(self.rows(replace(style, fields=fields)), varying=varying)
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
            return _Value(
                self.assign(f"Paint({channels}, 0.0, False, 0, 0, 0, 0, {name}, {size.code} / context.scale_factor)"),
                varying=varying,
            )
        stroke, fill = self.field(value, "stroke"), self.field(value, "fill")
        pen = ("0", "0", "0")
        width, dashed = "0.0", _FALSE
        if stroke.present != _FALSE:
            drawn = self.both(active, stroke.present)
            color = self.field(stroke, "color")
            assert color.items is not None
            amount = self.pixels(self.field(stroke, "width"), drawn, location)
            scaled = self.assign(f"{amount.code} / context.scale_factor")
            self.require(f"{scaled} >= 0", amount.varying, "Stroke width must be nonnegative.", drawn, location)
            pen = tuple(self.pick(stroke.present, item.code, "0") for item in color.items)  # type: ignore[assignment]
            width = self.pick(stroke.present, scaled, "0.0")
            dashed = self.pick(stroke.present, self.assign(f"{self.field(stroke, 'pattern').code} == 'dash'"), _FALSE)
        brush = ("0", "0", "0")
        alpha = "0"
        if fill.present != _FALSE:
            color = self.field(fill, "color")
            assert color.items is not None
            brush = tuple(self.pick(fill.present, item.code, "0") for item in color.items)  # type: ignore[assignment]
            alpha = self.pick(fill.present, self.field(fill, "alpha").code, "0")
        radius = "0.0"
        corner = self.field(value, "radius")
        if corner.present != _FALSE:
            rounded = self.both(active, corner.present)
            amount = self.pixels(corner, rounded, location)
            scaled = self.assign(f"{amount.code} / context.scale_factor")
            self.require(f"{scaled} >= 0", amount.varying, "Corner radius must be nonnegative.", rounded, location)
            radius = self.pick(corner.present, scaled, "0.0")
        return _Value(
            self.assign(
                f"Paint({', '.join(pen)}, {width}, {dashed}, {', '.join(brush)}, {alpha}, 'Arial', 0.02, {radius})"
            ),
            varying=varying,
        )

    # Kernels ----------------------------------------------------------------

    def pixels(self, length: _Value, active: str, location: str) -> _Value:
        assert length.fields is not None
        amount, unit = length.fields["value"], length.fields["unit"]
        varying = amount.varying or unit.varying
        if unit.const == "px":
            return _Value(amount.code, varying=amount.varying)
        if unit.const in ("image_width", "image_height", "image_min"):
            scale = {
                "image_width": "context.width",
                "image_height": "context.height",
                "image_min": "context.scale_factor",
            }
            return self.finite(f"{amount.code} * {scale[unit.const]}", varying, active, location)
        return self.finite(f"{amount.code} * unit_scale({unit.code}, context)", varying, active, location)

    def number(self, value: _Value, dimension: bool, active: str, location: str) -> _Value:
        return self.pixels(value, active, location) if dimension else value

    def nonnegative(self, value: _Value, active: str, location: str) -> _Value:
        self.require(f"{value.code} >= 0", value.varying, "Value must be nonnegative.", active, location)
        return value

    def point(self, x: _Value, y: _Value) -> _Value:
        return _Value(fields={"x": x, "y": y}, varying=x.varying or y.varying)

    def box(self, x: _Value, y: _Value, w: _Value, h: _Value) -> _Value:
        return _Value(fields={"x": x, "y": y, "w": w, "h": h}, varying=any(v.varying for v in (x, y, w, h)))

    def length(self, value: _Value) -> _Value:
        return _Value(fields={"value": value, "unit": self.constant("px")}, varying=value.varying)

    def anchor(self, value: _Value) -> tuple[_Value, _Value]:
        if value.const is not MISSING and value.const in ANCHORS:
            ax, ay = ANCHORS[value.const]
            return _Value(repr(ax)), _Value(repr(ay))
        factors = self.assign(f"anchor_factors({value.code})")
        return _Value(f"{factors}[0]", varying=value.varying), _Value(f"{factors}[1]", varying=value.varying)

    def node_kernel(self, node: Node, scope: _Scope, active: str, location: str, kind: ValueType | None) -> _Value:
        args = [self.expression(child, scope, active, location) for child in node.children]
        name = node.operand[0]
        varying = any(arg.varying for arg in args)
        if name == "is_present":
            return self.boolean(args[0].present, args[0].varying)
        if name == "is_not_empty":
            value = args[0]
            if value.items is not None:
                base = f"({value.size}) > 0" if value.size is not None else repr(bool(value.items))
            else:
                base = f"nonempty({value.code})"
            return self.boolean(
                self.both(value.present, self.assign(base) if base not in (_TRUE, _FALSE) else base), varying
            )
        if name == "gt":
            return self.boolean(f"({args[0].code}) > ({args[1].code})", varying)
        if name in ("add", "sub", "mul", "div", "min", "max", "clamp"):
            return self.arithmetic(node.operand, args, active, location)
        if name == "has_area":
            box = args[0]
            return self.boolean(f"({self.field(box, 'w').code} > 0) & ({self.field(box, 'h').code} > 0)", varying)
        if name == "offset_point":
            point = args[0]
            dx, dy = self.pixels(args[1], active, location), self.pixels(args[2], active, location)
            return self.point(
                self.finite(f"{self.field(point, 'x').code} + {dx.code} / context.width", varying, active, location),
                self.finite(f"{self.field(point, 'y').code} + {dy.code} / context.height", varying, active, location),
            )
        if name in ("box_anchor", "box_min_size", "resize_box", "scale_box", "place_box", "inset_box"):
            return self.box_operation(name, args, varying, active, location)
        if name == "vector_arrow":
            return self.vector_arrow(args, varying, active, location)
        if name == "trim_text":
            text, count, delimiter, suffix, elide = args
            self.require(
                f"{count.code} >= 0",
                count.varying,
                "Text length must be nonnegative.",
                self.both(active, text.present),
                location,
            )
            code = f"trim_text({text.code}, {count.code}, {delimiter.code}, {suffix.code}, {elide.code})"
            return _Value(self.assign(code), present=text.present, varying=varying)
        if name == "lookup_text":
            text, mapping, default = args
            if mapping.const is not MISSING:
                code = f"per_row({self.bind(kernels.text_table(mapping.const).get)}, {text.code}, {default.code})"
            else:
                code = f"lookup_rows({text.code}, {self.rows(mapping)}, {default.code})"
            return _Value(self.assign(code), present=text.present, varying=varying)
        if name == "number_text":
            value, precision, hide = args
            self.require(
                f"(0 <= {precision.code}) & ({precision.code} <= 100)",
                precision.varying,
                "Precision must be in [0,100].",
                self.both(active, value.present),
                location,
            )
            result = self.assign(f"number_text({value.code}, {precision.code}, {hide.code})")
            return _Value(
                self.assign(f"{result}[1]"),
                present=self.both(value.present, self.assign(f"{result}[0]")),
                varying=varying,
            )
        if name == "color_ramp":
            value, stops = args
            if stops.const is not MISSING:
                positions, colors = kernels.ramp_table(stops.const)
                channels = self.assign(f"ramp({value.code}, {self.bind(positions)}, {self.bind(colors)})")
            else:
                channels = self.assign(f"ramp_rows({value.code}, {self.rows(stops)})")
            items = tuple(_Value(self.assign(f"{channels}[{index}]"), varying=varying) for index in range(3))
            return _Value(items=items, varying=varying)
        if name == "pick_color":
            text, palette = args
            if palette.const is not MISSING:
                channels = self.assign(f"pick_color({text.code}, {self.bind(kernels.palette_table(palette.const))})")
            else:
                channels = self.assign(f"pick_color_rows({text.code}, {self.rows(palette)})")
            items = tuple(_Value(self.assign(f"{channels}[{index}]"), varying=varying) for index in range(3))
            return _Value(items=items, varying=varying)
        raise ValueError(f"Unknown operation: {name}")

    def arithmetic(self, operand: tuple[Any, ...], args: list[_Value], active: str, location: str) -> _Value:
        name, dimensions, result_length = operand[0], operand[1], operand[2]
        if name in ("add", "mul", "min", "max"):
            items = args[0].items
            assert items is not None
            operands = list(items)
        else:
            operands = list(args)
        numbers = [self.number(arg, dimension, active, location) for arg, dimension in zip(operands, dimensions)]
        codes = [number.code for number in numbers]
        varying = any(number.varying for number in numbers)
        if name == "sub":
            code = f"({codes[0]}) - ({codes[1]})"
        elif name == "div":
            self.require(f"({codes[1]}) != 0", numbers[1].varying, "Cannot divide by zero.", active, location)
            code = f"divide({codes[0]}, {codes[1]})"
        elif name == "clamp":
            self.require(f"({codes[1]}) <= ({codes[2]})", varying, "Clamp minimum exceeds maximum.", active, location)
            code = f"maximum({codes[1]}, minimum({codes[2]}, {codes[0]}))"
        elif name == "add":
            code = " + ".join(("0", *(f"({item})" for item in codes)))
        elif name == "mul":
            code = " * ".join(("1", *(f"({item})" for item in codes)))
        else:
            code = f"{'minimum' if name == 'min' else 'maximum'}({', '.join(codes)})"
        result = self.finite(code, varying, active, location)
        return self.length(result) if result_length else result

    def box_operation(self, name: str, args: list[_Value], varying: bool, active: str, location: str) -> _Value:
        box = args[0]
        x, y, w, h = (self.field(box, key).code for key in ("x", "y", "w", "h"))
        if name == "box_min_size":
            return self.length(
                self.finite(f"minimum({w} * context.width, {h} * context.height)", varying, active, location)
            )
        if name == "inset_box":
            margin, fraction = args[1:]
            self.require(
                f"({margin.present}) != ({fraction.present})",
                margin.varying or fraction.varying,
                "inset_box requires exactly one of margin or fraction.",
                active,
                location,
            )
            by_fraction = self.both(active, fraction.present)
            by_margin = self.both(active, self.negate(fraction.present))
            dx = dy = "0.0"
            if by_fraction != _FALSE:
                self.require(
                    f"(0 <= {fraction.code}) & ({fraction.code} <= 0.5)",
                    fraction.varying,
                    "Inset fraction must be in [0,0.5].",
                    by_fraction,
                    location,
                )
                dx, dy = self.assign(f"{w} * {fraction.code}"), self.assign(f"{h} * {fraction.code}")
            if by_margin != _FALSE:
                amount = self.nonnegative(self.pixels(margin, by_margin, location), by_margin, location)
                mx = self.assign(f"minimum({w} / 2, {amount.code} / context.width)")
                my = self.assign(f"minimum({h} / 2, {amount.code} / context.height)")
                dx, dy = self.pick(fraction.present, dx, mx), self.pick(fraction.present, dy, my)
            return self.numeric_box(
                (f"{x} + {dx}", f"{y} + {dy}", f"maximum(0, {w} - 2 * {dx})", f"maximum(0, {h} - 2 * {dy})"),
                varying,
                active,
                location,
            )
        ax, ay = self.anchor(args[1 if name == "box_anchor" else 3])
        varying = varying or ax.varying
        if name == "box_anchor":
            return self.point(
                self.finite(f"{x} + {w} * {ax.code}", varying, active, location),
                self.finite(f"{y} + {h} * {ay.code}", varying, active, location),
            )
        if name == "scale_box":
            width = self.assign(f"{w} * {self.nonnegative(args[1], active, location).code}")
            height = self.assign(f"{h} * {self.nonnegative(args[2], active, location).code}")
        else:
            sizes = []
            for argument, original, extent in ((args[1], w, "context.width"), (args[2], h, "context.height")):
                rows = self.both(active, argument.present) if name == "resize_box" else active
                if rows == _FALSE:
                    sizes.append(original)
                    continue
                amount = self.nonnegative(self.pixels(argument, rows, location), rows, location)
                scaled = self.assign(f"{amount.code} / {extent}")
                sizes.append(self.pick(argument.present, scaled, original) if name == "resize_box" else scaled)
            width, height = sizes
        left = f"{x} + ({w} - {width}) * {ax.code}"
        top = f"{y} + ({h} - {height}) * {ay.code}"
        if name == "place_box":
            margin = self.nonnegative(self.pixels(args[4], active, location), active, location)
            left = f"{left} + (1 - 2 * {ax.code}) * {margin.code} / context.width"
            top = f"{top} + (1 - 2 * {ay.code}) * {margin.code} / context.height"
        return self.numeric_box((left, top, width, height), varying, active, location)

    def numeric_box(self, codes: tuple[str, str, str, str], varying: bool, active: str, location: str) -> _Value:
        x, y, w, h = (self.finite(code, varying, active, location) for code in codes)
        return self.box(x, y, w, h)

    def vector_arrow(self, args: list[_Value], varying: bool, active: str, location: str) -> _Value:
        origin, vector, gain, maximum, head, half, threshold = args
        gain = self.nonnegative(gain, active, location)
        limits = [
            self.nonnegative(self.pixels(length, active, location), active, location)
            for length in (maximum, head, half)
        ]
        threshold = self.nonnegative(threshold, active, location)
        ox, oy = self.field(origin, "x").code, self.field(origin, "y").code
        vx, vy = self.field(vector, "vx").code, self.field(vector, "vy").code
        result = self.assign(
            f"arrow({ox}, {oy}, {vx}, {vy}, {gain.code}, {limits[0].code}, {limits[1].code}, {limits[2].code}, "
            f"{threshold.code}, context.width, context.height)"
        )
        drawn = self.both(active, vector.present)
        self.require(f"{result}[1]", varying, "Numeric result must be finite.", drawn, location)
        parts = [_Value(self.assign(f"{result}[{index}]"), varying=varying) for index in range(2, 8)]
        end = self.point(parts[0], parts[1])
        head_points = _Value(
            items=(end, self.point(parts[2], parts[3]), self.point(parts[4], parts[5])),
            varying=varying,
            size=self.assign(f"np.where({result}[8], 3, 0)"),
        )
        present = self.both(vector.present, self.assign(f"{result}[0]"))
        start = replace(origin, present=_TRUE)
        return _Value(fields={"start": start, "end": end, "head_points": head_points}, present=present, varying=varying)

    # Programs ---------------------------------------------------------------

    def program(self, program: Program, scope: _Scope, active: str) -> None:
        enabled = self.expression(program.enabled, scope, active)
        rows = self.both(active, enabled.code)
        if rows == _FALSE:
            return
        self.line(f"if any_rows({rows}):")
        self.level += 1
        self.line("pass")
        for step in program.steps:
            condition = self.expression(step.enabled, scope, rows)
            selected = self.both(rows, condition.code)
            if selected == _FALSE:
                continue
            self.line(f"if any_rows({selected}):")
            self.level += 1
            self.line("pass")
            values = self.expression(step.fields, scope, selected)
            target = step.target
            if target is not None:
                self.program(
                    target, _Scope(target.values, {"parameters": values, "context": scope.inputs["context"]}), selected
                )
            else:
                assert step.style is not None
                paint = self.expression(step.style, scope, selected, step.location)
                self.primitive(step.primitive, values, paint, selected, step.location)
            self.level -= 1
        self.level -= 1

    def primitive(self, kind: str, fields: _Value, paint: _Value, rows: str, location: str) -> None:
        assert fields.items is not None
        first = fields.items[0]
        if kind in ("polygon", "polyline"):
            if first.items is not None:
                points = ", ".join(
                    f"({self.field(item, 'x').code}, {self.field(item, 'y').code})" for item in first.items
                )
                argument = self.assign(
                    f"{self.bind(kernels.PointRows)}(({points}{',' if first.items else ''}), {first.size or 'None'})"
                )
            else:
                argument = self.assign(f"{self.bind(kernels.PointRows)}(collections={first.code})")
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
                f"per_row({self.bind(kernels.label_content)}, {paint.code}, {self.rows(fields.items[2])}, {active})"
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


_HELPERS = (
    "fire",
    "relay",
    "bad",
    "failure",
    "any_rows",
    "invert",
    "select",
    "finite",
    "minimum",
    "maximum",
    "divide",
    "unit_scale",
    "anchor_factors",
    "nonempty",
    "lengths",
    "isin",
    "per_row",
    "arrow",
    "ramp",
    "ramp_rows",
    "pick_color",
    "pick_color_rows",
    "trim_text",
    "number_text",
    "lookup_rows",
    "get",
)


def _rebuild(structure: object) -> Callable[..., object]:
    """Return a function rebuilding one row's canonical value from its leaf values."""

    def build(node: Any, leaves: tuple[object, ...]) -> object:
        if node[-1] is not None and not leaves[node[-1]]:
            return None
        if node[0] == "leaf":
            return leaves[node[1]]
        if node[0] == "fields":
            return {key: build(child, leaves) for key, child in node[1]}
        items = tuple(build(child, leaves) for child in node[1])
        return items if node[2] is None else items[: int(cast(int, leaves[node[2]]))]

    return lambda *leaves: build(structure, leaves)


def _converter(descriptor: tuple[Any, ...]) -> Callable[[Any], Any]:
    mode, kind, transforms, missing, child, spec = descriptor
    if mode == 0:
        return compile_validator(spec)
    if mode == 1:
        conversions = tuple((key, _converter(conversion)) for key, conversion in transforms)

        def convert_record(value: Any) -> Any:
            if value is None:
                return None
            result = dict(value)
            for key, convert in conversions:
                result[key] = convert(result[key])
            for key in missing:
                result[key] = None
            return check_constraints(result, kind)

        return convert_record
    convert_item = _converter(child) if child else None

    def convert_items(value: Any) -> Any:
        if value is None:
            return None
        return check_constraints(tuple(convert_item(item) if convert_item else item for item in value), kind)

    return convert_items


def _context() -> _Value:
    return _Value(fields={name: _Value(f"context.{name}") for name, _ in CONTEXT.fields})


def compile_rows(program: Program, roots: Mapping[str, ValueType], *, table: bool) -> Callable[..., Any]:
    """Compile a program for a table of rows, or for one row of shared mapping inputs.

    The function has the signature ``update(inputs, context, state)`` and records failures and
    primitive batches in the ``RowState``.
    """
    builder = _Builder(table, roots)
    builder.program(program, _Scope(program.values, {"context": _context()}), _TRUE)
    return builder.finish()


def evaluate(plan: Node, context: object) -> object:
    """Evaluate a compile-time constant through the generated shared-value semantics."""
    builder = _Builder(False, {})
    result = builder.materialize(builder.expression(plan, _Scope((), {"context": _context()}), _TRUE))
    state = RowState(1)
    with np.errstate(all="ignore"):
        value = builder.finish(result)({}, context, state)
    if state.hits:
        raise state.first_error()
    return value
