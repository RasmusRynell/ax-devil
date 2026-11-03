"""Validation of runtime values against the closed value types, and the bounds a type or parameter declares."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import lru_cache
from types import MappingProxyType
from typing import Any, cast

from .definitions import MAX_COLLECTION, QUANTITIES, ValueType
from .values import TemplateRuntimeError, number_value, record, sequence


def field_label(kind: ValueType, key: str, label: str) -> str:
    """Name a record field in errors: a quantity's number takes the quantity's name, other fields their key."""
    return label if QUANTITIES.get(kind.name) == key else key


def accepted_bounds(kind: ValueType) -> tuple[str, ...]:
    """Return the bounds a parameter of *kind* may declare: numeric ones for numbers and lengths, one for text."""
    if kind.name == "string":
        return ("max_length",)
    if kind.name in ("number", "integer") or kind.name in QUANTITIES:
        return ("minimum", "exclusive_minimum", "maximum")
    return ()


def bounded(kind: ValueType, bounds: Mapping[str, object], location: str = "$") -> ValueType:
    """Return *kind* limited by a parameter declaration's *bounds*.

    Numbers and text take the bounds themselves; a quantity such as a length takes them on its numeric field.
    """
    if not bounds:
        return kind
    accepted = accepted_bounds(kind)
    for name, value in bounds.items():
        if name not in accepted:
            raise TemplateRuntimeError(
                f"A {kind.name} parameter cannot declare {name}; it accepts {', '.join(accepted) or 'no bounds'}.",
                location=f"{location}.{name}",
            )
        if name == "max_length":
            if type(value) is not int or value < 0:
                raise TemplateRuntimeError("max_length must be a nonnegative integer.", location=f"{location}.{name}")
        else:
            number_value(value, name)
    field_name = QUANTITIES.get(kind.name)
    if field_name is None:
        return replace(kind, **cast(dict[str, Any], bounds))
    fields = tuple(
        (name, replace(child, **cast(dict[str, Any], bounds)) if name == field_name else child)
        for name, child in kind.fields
    )
    return replace(kind, fields=fields)


def validate_value(value: object, expected: ValueType, label: str | None = None) -> object:
    """Validate runtime boundary values and construct immutable typed records.

    *label* names the value in bound errors; record fields use their key and list items their list's name.
    """
    label = label or expected.name
    if value is None:
        if expected.nullable:
            return None
        raise TemplateRuntimeError(f"Expected {expected.name}, got null.")
    if expected.name == "any":
        return value
    if expected.name in ("number", "integer"):
        number_value(value)
        if expected.name == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise TemplateRuntimeError("Expected an integer.")
        return _check_own(value, expected, label)
    if expected.name == "bool":
        if not isinstance(value, bool):
            raise TemplateRuntimeError("Expected a boolean.")
        return value
    if expected.name == "string" or expected.choices:
        if not isinstance(value, str) or (expected.choices and value not in expected.choices):
            raise TemplateRuntimeError(f"Expected {expected.name}: {expected.choices or 'text'}.")
        return _check_own(value, expected, label)
    if expected.fields or expected.name in ("record", "parameters", "bindings"):
        if not isinstance(value, Mapping):
            raise TemplateRuntimeError(f"Expected {expected.name} record.")
        unknown = set(value).difference(key for key, _ in expected.fields)
        if unknown:
            raise TemplateRuntimeError(f"Unknown {expected.name} fields: {sorted(unknown)}.")
        result = MappingProxyType(
            {
                key: validate_value(value.get(key), kind, field_label(expected, key, label))
                for key, kind in expected.fields
            }
        )
        return _check_own(result, expected, label)
    if expected.item is not None:
        if not isinstance(value, (list, tuple)) or len(value) > MAX_COLLECTION:
            raise TemplateRuntimeError(f"Expected {expected.name} with at most {MAX_COLLECTION} items.")
        result_items = tuple(validate_value(item, expected.item, f"{label} item") for item in value)
        return _check_own(result_items, expected, label)
    raise TemplateRuntimeError(f"Unsupported value type: {expected.name}.")


@lru_cache(maxsize=256)
def compile_validator(expected: ValueType, label: str | None = None) -> Callable[[object], object]:
    """Resolve structural checks once for a demanded external value; *label* is as for ``validate_value``."""
    name = expected.name
    label = label or name
    validate: Callable[[object], object]
    if expected.fields or name in ("record", "parameters", "bindings"):
        fields = tuple(
            (key, compile_validator(kind, field_label(expected, key, label))) for key, kind in expected.fields
        )
        allowed = frozenset(key for key, _ in fields)

        def validate(value: object) -> object:
            if not isinstance(value, Mapping):
                raise TemplateRuntimeError(f"Expected {name} record.")
            unknown = value.keys() - allowed
            if unknown:
                raise TemplateRuntimeError(f"Unknown {name} fields: {sorted(unknown)}.")
            result = MappingProxyType({key: check(value.get(key)) for key, check in fields})
            return _check_own(result, expected, label)
    elif expected.item is not None:
        item_validator = compile_validator(expected.item, f"{label} item")

        def validate(value: object) -> object:
            if not isinstance(value, (list, tuple)) or len(value) > MAX_COLLECTION:
                raise TemplateRuntimeError(f"Expected {name} with at most {MAX_COLLECTION} items.")
            return _check_own(tuple(item_validator(item) for item in value), expected, label)
    else:
        # Scalar validation has no recursive structure to resolve.
        return lambda value: validate_value(value, expected, label)

    if expected.nullable:
        return lambda value: None if value is None else validate(value)

    def required(value: object) -> object:
        if value is None:
            raise TemplateRuntimeError(f"Expected {name}, got null.")
        return validate(value)

    return required


def allowed_values(kind: ValueType) -> str:
    """Describe the values *kind*'s bounds allow, for error messages and editor hints."""
    parts = []
    if kind.minimum is not None:
        parts.append(f"at least {kind.minimum:g}")
    if kind.exclusive_minimum is not None:
        parts.append(f"greater than {kind.exclusive_minimum:g}")
    if kind.maximum is not None:
        parts.append(f"at most {kind.maximum:g}")
    if kind.max_length is not None:
        parts.append(f"at most {kind.max_length} characters long")
    if kind.min_items is not None and kind.min_items == kind.max_items:
        parts.append(f"a list of exactly {kind.min_items} items")
    elif kind.min_items is not None and kind.max_items is not None:
        parts.append(f"a list of {kind.min_items} to {kind.max_items} items")
    elif kind.min_items is not None:
        parts.append(f"a list of at least {kind.min_items} items")
    elif kind.max_items is not None:
        parts.append(f"a list of at most {kind.max_items} items")
    return " and ".join(parts)


def within_bounds(value: object, kind: ValueType) -> bool:
    """Return whether a scalar, or a list's item count, is within the bounds *kind* declares."""
    if isinstance(value, str):
        return kind.max_length is None or len(value) <= kind.max_length
    if isinstance(value, (tuple, list)):
        count = len(value)
        return (kind.min_items is None or count >= kind.min_items) and (
            kind.max_items is None or count <= kind.max_items
        )
    number = cast(float, value)
    return (
        (kind.minimum is None or number >= kind.minimum)
        and (kind.exclusive_minimum is None or number > kind.exclusive_minimum)
        and (kind.maximum is None or number <= kind.maximum)
    )


def _check_bounds(value: object, kind: ValueType, label: str) -> None:
    if kind.bounds and not within_bounds(value, kind):
        raise TemplateRuntimeError(f"{label} must be {allowed_values(kind)}.")


def _check_own(value: object, kind: ValueType, label: str) -> object:
    """Check the bounds *kind* declares for the value itself and the rule bounds cannot express."""
    _check_bounds(value, kind, label)
    rule = RULES.get(kind.name)
    if rule is not None:
        rule(value)
    return value


def check_constraints(value: object, kind: ValueType) -> object:
    """Check a value's own bounds and rule, and the bounds of its direct scalar fields or items.

    Records and lists check their scalar parts because the compiler checks scalars as part of the value that holds
    them. Nested records and lists are checked when they are built.
    """
    if value is None:
        return value
    if kind.item is not None and kind.item.is_scalar and kind.item.bounds:
        for item in sequence(value):
            _check_bounds(item, kind.item, f"{kind.name} item")
    for key, field_kind in kind.fields:
        field = record(value)[key]
        if field is not None and field_kind.is_scalar:
            _check_bounds(field, field_kind, field_label(kind, key, kind.name))
    return _check_own(value, kind, kind.name)


def _nonempty_points_minimum(minimum: int) -> Callable[[object], None]:
    def check(value: object) -> None:
        points = sequence(record(value)["points"])
        if points and len(points) < minimum:
            raise TemplateRuntimeError(f"Geometry requires at least {minimum} points.")

    return check


def _increasing_stops(value: object) -> None:
    stops = sequence(value)
    if any(cast(float, record(a)["at"]) >= cast(float, record(b)["at"]) for a, b in zip(stops, stops[1:])):
        raise TemplateRuntimeError("Color stops must be strictly increasing.")


def _unique_keys(value: object) -> None:
    keys = [record(entry)["key"] for entry in sequence(value)]
    if len(set(keys)) != len(keys):
        raise TemplateRuntimeError("Text mapping keys must be unique.")


RULES: Mapping[str, Callable[[object], None]] = MappingProxyType(
    {
        "polygon_fields": _nonempty_points_minimum(3),
        "polyline_fields": _nonempty_points_minimum(2),
        "color_stops": _increasing_stops,
        "text_mapping": _unique_keys,
    }
)
"""Checks that bounds cannot express, keyed by the type they belong to."""


@lru_cache(maxsize=256)
def has_constraints(kind: ValueType) -> bool:
    """Return whether *kind* declares bounds or a rule for itself or for a direct scalar field or item."""
    parts = (*(field for _, field in kind.fields), *((kind.item,) if kind.item is not None else ()))
    return bool(kind.bounds) or kind.name in RULES or any(part.is_scalar and part.bounds for part in parts)
