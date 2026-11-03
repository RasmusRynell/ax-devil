"""Compile-time values: shared Python values or per-row arrays, with the structure of their type."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from .definitions import ValueType
from .lowering_writer import FALSE, MISSING, TRUE, CodeWriter

_NUMBERS = ("number", "integer")


@dataclass(slots=True)
class Value:
    """Compile-time description of a value: a leaf expression, record fields or list items.

    ``present`` is the null mask expression; ``varying`` means some part differs between rows.
    ``size`` is a per-row item count for lists whose trailing items may be absent.
    """

    code: str = "None"
    fields: dict[str, Value] | None = None
    items: tuple[Value, ...] | None = None
    present: str = TRUE
    varying: bool = False
    const: object = MISSING
    size: str | None = None


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


class ValueBuilder(CodeWriter):
    """Builds and reshapes ``Value`` descriptions of records, lists and leaves."""

    def finite(self, code: str, varying: bool, active: str, location: str) -> Value:
        result = self.assign(code)
        self.require(f"finite({result})", varying, "Numeric result must be finite.", active, location)
        return Value(result, varying=varying)

    def boolean(self, code: str, varying: bool) -> Value:
        if code in (TRUE, FALSE):
            return Value(code, const=code == TRUE)
        return Value(self.assign(code), varying=varying)

    def constant(self, value: object) -> Value:
        if value is None:
            return Value("None", present=FALSE, const=None)
        if isinstance(value, Mapping):
            fields = {str(key): self.constant(item) for key, item in value.items()}
            return Value(fields=fields, const=value)
        if isinstance(value, (tuple, list)):
            return Value(items=tuple(self.constant(item) for item in value), const=value)
        if isinstance(value, bool):
            return Value(repr(value), const=value)
        return Value(self.bind(value), const=value)

    def placeholder(self, kind: ValueType) -> Value:
        if kind.fields:
            return Value(fields={key: self.placeholder(child) for key, child in kind.fields}, present=FALSE, const=None)
        if kind.name == "rgb":
            return Value(items=tuple(Value("0") for _ in range(3)), present=FALSE, const=None)
        value = _placeholder(kind)
        return Value("None" if value is None else self.bind(value), present=FALSE, const=None)

    def conform(self, value: Value, kind: ValueType | None) -> Value:
        """Give a value the structure of its type, with typed placeholders for null parts."""
        if kind is None or kind.name in ("any", "null"):
            return value
        if kind.fields:
            if value.fields is None:
                if value.present == FALSE:
                    return self.placeholder(kind)
                return self.present_in(self.destructure(value.code, kind, True), value.present)
            fields = {
                key: self.conform(value.fields.get(key, Value("None", present=FALSE, const=None)), child)
                for key, child in kind.fields
            }
            return replace(value, fields=fields, varying=value.varying or any(item.varying for item in fields.values()))
        if kind.name == "rgb" and value.items is None:
            if value.present == FALSE:
                return self.placeholder(kind)
            return self.present_in(self.destructure(value.code, kind, True), value.present)
        if kind.item is None and value.fields is None and value.items is None and value.code == "None":
            placeholder = _placeholder(kind)
            if placeholder is not None:
                return replace(value, code=self.bind(placeholder))
        return value

    def present_in(self, value: Value, present: str) -> Value:
        return replace(value, present=self.both(present, value.present))

    def destructure(self, code: str, kind: ValueType, nullable: bool | None = None) -> Value:
        """Describe a shared canonical value (mappings and tuples) by its type; null parts get placeholders."""
        value = self.assign(code)
        if nullable is None:
            nullable = kind.nullable or kind.name == "any"
        exists = self.assign(f"{value} is not None") if nullable else TRUE
        if kind.fields:
            fields = {
                key: self.destructure(f"get({value}, {self.bind(key)})", child, nullable or None)
                for key, child in kind.fields
            }
            return Value(fields=fields, present=exists)
        if kind.name == "rgb":
            items = tuple(Value(self.assign(f"0 if {value} is None else {value}[{index}]")) for index in range(3))
            return Value(items=items, present=exists)
        placeholder = _placeholder(kind)
        if nullable and placeholder is not None:
            value = self.assign(f"{self.bind(placeholder)} if {value} is None else {value}")
        return Value(value, present=exists)

    def columns(self, code: str, kind: ValueType) -> Value:
        """Describe a table column ``(present, data)`` by its type."""
        column = self.assign(code)
        present = self.assign(f"{column}[0]")
        data = f"{column}[1]"
        if kind.fields:
            fields = {key: self.columns(f"{data}[{self.bind(key)}]", child) for key, child in kind.fields}
            return Value(fields=fields, present=present, varying=True)
        if kind.name == "rgb":
            items = tuple(Value(self.assign(f"{data}[{index}]"), varying=True) for index in range(3))
            return Value(items=items, present=present, varying=True)
        return Value(self.assign(data), present=present, varying=True)

    def materialize(self, value: Value) -> str:
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
        return inner if value.present == TRUE else f"({inner} if {value.present} else None)"

    def rows(self, value: Value) -> str:
        """Build per-row canonical values of a varying value (present parts only)."""
        if not value.varying:
            return self.materialize(value)
        leaves: list[str] = []

        def leaf(code: str) -> int:
            leaves.append(code)
            return len(leaves) - 1

        def shape(item: Value) -> tuple[Any, ...]:
            if item.fields is not None:
                node: tuple[Any, ...] = ("fields", tuple((key, shape(child)) for key, child in item.fields.items()))
            elif item.items is not None:
                size = leaf(item.size) if item.size is not None else None
                node = ("items", tuple(shape(child) for child in item.items), size)
            else:
                node = ("leaf", leaf(item.code))
            return (*node, leaf(item.present) if item.present != TRUE else None)

        structure = shape(value)
        return self.assign(f"per_row({self.bind(_rebuild(structure))}, {', '.join(leaves)})")

    def store(self, value: Value) -> Value:
        """Copy every part of a value into locals, so later code can reassign them."""
        if value.fields is not None:
            fields = {key: self.store(item) for key, item in value.fields.items()}
            return Value(fields=fields, present=self.local(value.present), varying=value.varying, const=value.const)
        if value.items is not None:
            items = tuple(self.store(item) for item in value.items)
            size = self.local(value.size) if value.size is not None else None
            return Value(
                items=items, present=self.local(value.present), varying=value.varying, const=value.const, size=size
            )
        return Value(
            self.local(value.code), present=self.local(value.present), varying=value.varying, const=value.const
        )

    def local(self, code: str) -> str:
        name = self.unique()
        self.line(f"{name} = {code}")
        return name

    def copy(self, target: Value, source: Value) -> None:
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

    def same_shape(self, first: Value, second: Value) -> bool:
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

    def normalize(self, value: Value, kind: ValueType | None) -> Value:
        """Represent collections of branch values as per-row canonical values, so branches share a structure."""
        if kind is None:
            return value
        if kind.fields and value.fields is not None:
            fields = {
                key: self.normalize(value.fields[key], child) for key, child in kind.fields if key in value.fields
            }
            return replace(value, fields=fields)
        if kind.item is not None and kind.name != "rgb" and value.items is not None:
            complete = replace(value, present=TRUE)
            code = self.rows(complete) if value.varying else self.materialize(complete)
            return Value(self.assign(code), present=value.present, varying=value.varying)
        return value

    def merge(self, condition: str, then: Value, other: Value, kind: ValueType | None) -> Value:
        """Choose per row between two values of one type."""
        then, other = self.normalize(then, kind), self.normalize(other, kind)
        assert self.same_shape(then, other)
        present = self.pick(condition, then.present, other.present)
        if then.fields is not None and other.fields is not None:
            fields = {
                key: self.merge(condition, item, other.fields[key], kind.field(key) if kind and kind.fields else None)
                for key, item in then.fields.items()
            }
            return Value(fields=fields, present=present, varying=True)
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
            return Value(items=items, present=present, varying=True, size=size)
        if then.present == FALSE:
            code = other.code
        elif other.present == FALSE:
            code = then.code
        else:
            code = self.pick(condition, then.code, other.code)
        return Value(code, present=present, varying=True)

    def field(self, value: Value, key: str) -> Value:
        if value.fields is None:
            return Value(self.assign(f"get({self.materialize(value)}, {self.bind(key)})"), present=TRUE)
        item = value.fields[key]
        return replace(item, present=self.both(value.present, item.present), varying=item.varying or value.varying)
