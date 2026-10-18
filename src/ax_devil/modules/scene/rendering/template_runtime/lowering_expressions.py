"""Lowering of plan nodes to values: references, branches, validation, constraints and conversion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, cast

from .definitions import ValueType
from .lowering_values import Value, ValueBuilder
from .lowering_writer import FALSE, TRUE
from .plan import Node
from .validation import RULES, allowed_values, check_constraints, compile_validator, field_label, within_bounds


@dataclass(slots=True)
class Slot:
    """Memoized calculated value: its computed flag, its lowered ``Value`` and the failure list it collected."""

    flag: str
    value: Value
    terms: str


@dataclass(slots=True)
class Scope:
    """What a lowered program sees: its calculated-value nodes, input values by root name and memo slots by index."""

    values: tuple[Node, ...]
    inputs: dict[str, Value]
    slots: dict[int, Slot] = field(default_factory=dict)


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


class ExpressionBuilder(ValueBuilder):
    """Lowers expression nodes; ``expression`` dispatches to the ``node_*`` handlers of the leaf class."""

    def external(self, node: Node, scope: Scope) -> tuple[str, ...] | None:
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

    def reference(self, path: tuple[str, ...], active: str, location: str) -> Value:
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
        self, node: Node, scope: Scope, active: str, location: str = "$", kind: ValueType | None = None
    ) -> Value:
        if node.kind == "located":
            location = node.location if node.location != "$" else location
            return self.conform(self.expression(node.children[0], scope, active, location, node.operand), node.operand)
        path = self.external(node, scope)
        if path is not None:
            return self.reference(path, active, location)
        handler = getattr(self, f"node_{node.kind}", None)
        if handler is None:
            raise ValueError(f"Unknown compiler instruction: {node.kind}")
        return cast(Value, handler(node, scope, active, location, kind))

    def node_constant(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        return self.constant(node.operand)

    def node_slot(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
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
        value = self.expression(scope.values[node.operand], scope, TRUE, "$")
        self.sink = saved
        if slot is None:
            slot = Slot(flag, self.store(value), terms)
            scope.slots[node.operand] = slot
        else:
            self.copy(slot.value, value)
        self.line(f"{flag} = None")
        self.level -= 1
        self.line(f"relay(S, {self.sink or 'None'}, {terms}, {active}, {location!r})")
        return slot.value

    def node_input(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        value = scope.inputs[node.operand[0]]
        for key in node.operand[1:]:
            value = self.field(value, key)
        return value

    def node_access(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        value = self.expression(node.children[0], scope, active, location)
        for key in node.operand:
            value = self.field(value, key)
        return value

    def node_record(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        children = [self.expression(child, scope, active, location) for child in node.children]
        return Value(fields=dict(zip(node.operand, children)), varying=any(child.varying for child in children))

    def node_tuple(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        children = tuple(self.expression(child, scope, active, location) for child in node.children)
        return Value(items=children, varying=any(child.varying for child in children))

    def node_fields(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        value = self.expression(node.children[0], scope, active, location)
        items = tuple(self.field(value, key) for key in node.operand)
        return Value(items=items, varying=value.varying)

    def node_if(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
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

    def node_coalesce(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        result = self.conform(self.expression(node.children[0], scope, active, location, kind), kind)
        for child in node.children[1:]:
            if result.present == TRUE:
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

    def node_validate(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
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
            value = replace(value, present=TRUE)
        return value

    def node_constraint(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        value = self.expression(node.children[0], scope, active, location)
        self.constraint(value, node.operand, active, location)
        return value

    def constraint(self, value: Value, kind: ValueType, active: str, location: str) -> None:
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
                self.require(FALSE, False, f"{kind.name} must be {allowed_values(kind)}.", rows, location)
                return
            if value.size is not None and kind.bounds:
                self.bounds(Value(value.size, varying=varying), kind, kind.name, rows, location)
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

    def bounds(self, value: Value, kind: ValueType, label: str, rows: str, location: str) -> None:
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

    def checked_in_python(self, value: Value, kind: ValueType, rows: str, location: str) -> None:
        self.line("try:")
        self.level += 1
        self.line(f"{self.bind(check_constraints)}({self.materialize(value)}, {self.bind(kind)})")
        self.level -= 1
        self.line("except Exception as error:")
        self.level += 1
        self.report(rows, f"failure(error, {location!r})")
        self.level -= 1

    def count_items(self, value: Value) -> str:
        if value.items is not None:
            return value.size if value.size is not None else str(len(value.items))
        return self.assign(f"lengths({value.code})")

    def node_convert(self, node: Node, scope: Scope, active: str, location: str, kind: ValueType | None) -> Value:
        value = self.expression(node.children[0], scope, active, location)
        return self.convert(value, node.operand, active, location)

    def convert(self, value: Value, descriptor: tuple[Any, ...], active: str, location: str) -> Value:
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
                fields[key] = Value("None", present=FALSE, const=None)
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
            return Value(self.assign(f"per_row({converter}, {self.rows(value)})"), present=value.present, varying=True)
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
        return Value(converted, present=value.present)
