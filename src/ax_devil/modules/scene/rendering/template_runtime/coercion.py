"""Type compatibility, branch unions and the conversions that make an expression fit an expected type."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from .definitions import ValueType
from .expressions import UNSET, CompiledExpression, instruction
from .plan import Node
from .validation import has_constraints, validate_value
from .values import TemplateRuntimeError


def record_evaluator(fields: tuple[tuple[str, CompiledExpression], ...]) -> Node:
    return instruction("record", tuple(key for key, _ in fields), tuple(child.code for _, child in fields))


def compatible(actual: ValueType, expected: ValueType) -> bool:
    """Return whether a value of type *actual* can be used where *expected* is required."""
    if expected.name == "any":
        return True
    if actual.nullable and not expected.nullable:
        return False
    if actual.name == "null":
        return expected.nullable
    if actual.name == expected.name or (actual.name == "integer" and expected.name == "number"):
        return True
    if expected.choices:
        return actual.name == "string" or bool(actual.choices)
    if expected.name == "string" and actual.choices:
        return True
    if expected.fields and actual.fields:
        fields = dict(actual.fields)
        return not set(fields).difference(key for key, _ in expected.fields) and all(
            (key not in fields and kind.nullable) or (key in fields and compatible(fields[key], kind))
            for key, kind in expected.fields
        )
    if expected.item is not None and actual.item is not None:
        return compatible(actual.item, expected.item) or actual.item.name == "empty"
    return False


def union(left: ValueType, right: ValueType, location: str) -> ValueType:
    if left.name == "null":
        return right.optional()
    if right.name == "null":
        return left.optional()
    nullable = left.nullable or right.nullable
    a, b = left.present(), right.present()
    if a.name == b.name == "record":
        # Record literals join field by field; a field only some of them have is nullable.
        left_fields, right_fields = dict(a.fields), dict(b.fields)
        fields = tuple(
            (key, union(left_fields.get(key, ValueType("null")), right_fields.get(key, ValueType("null")), location))
            for key in {**left_fields, **right_fields}
        )
        return ValueType("record", fields, nullable=nullable)
    if compatible(a, b):
        return replace(b, nullable=nullable)
    if compatible(b, a):
        return replace(a, nullable=nullable)
    raise TemplateRuntimeError(
        f"Incompatible branch types {left.name} and {right.name}.", code="type_mismatch", location=location
    )


def coerce(expression: CompiledExpression, expected: ValueType, *, member: bool = False) -> CompiledExpression:
    """Convert *expression* to *expected*, adding the checks its bounds need.

    A bounded number or text is checked by itself, unless it is a *member* of a record or list, whose own check
    covers its scalar parts.
    """
    if expected.name == "any":
        return expression
    if not expression.value_type.nullable:
        expected = expected.present()
    if expression.constant is not UNSET:
        try:
            value = validate_value(expression.constant, expected)
        except TemplateRuntimeError as exc:
            raise TemplateRuntimeError(exc.diagnostic.message, location=expression.location) from exc
        return CompiledExpression(expected, instruction("constant", value), expression.location, constant=value)
    if not compatible(expression.value_type, expected):
        actual_name = f"{expression.value_type.name}{'|null' if expression.value_type.nullable else ''}"
        raise TemplateRuntimeError(
            f"Expected {expected.name}{'|null' if expected.nullable else ''}, got {actual_name}.",
            code="type_mismatch",
            location=expression.location,
        )
    if expression.value_type == expected:
        return expression
    if expression.members is not None and expected.fields:
        supplied = dict(expression.members)
        members = tuple(
            (
                key,
                coerce(supplied[key], kind, member=True)
                if key in supplied
                else CompiledExpression(kind, instruction("constant", None), expression.location, constant=None),
            )
            for key, kind in expected.fields
        )
        if all(child.constant is not UNSET for _, child in members):
            value = validate_value(MappingProxyType({key: child.constant for key, child in members}), expected)
            return CompiledExpression(expected, instruction("constant", value), expression.location, constant=value)
        callback = record_evaluator(members)
        if has_constraints(expected):
            callback = instruction("constraint", expected, (callback,))

        return CompiledExpression(
            expected,
            callback,
            expression.location,
            uses_context=expression.uses_context,
            uses_inputs=expression.uses_inputs,
            members=members,
        )
    converter = _conversion(expression.value_type, expected)
    if converter is None:
        if expected.is_scalar and expected.bounds and not member:
            checked = instruction("constraint", expected, (expression.code,))
            return replace(expression, value_type=expected, plan=checked)
        return replace(expression, value_type=expected)
    return CompiledExpression(
        expected,
        instruction("convert", converter, (expression.code,)),
        expression.location,
        uses_context=expression.uses_context,
        uses_inputs=expression.uses_inputs,
    )


def _conversion(actual: ValueType, expected: ValueType) -> tuple[object, ...] | None:
    if actual == expected or expected.name == "any":
        return None
    if expected.fields:
        actual_fields = dict(actual.fields)
        converters = tuple(
            (name, _conversion(actual_fields[name], kind)) for name, kind in expected.fields if name in actual_fields
        )
        transforms = tuple((name, converter) for name, converter in converters if converter is not None)
        missing = tuple(name for name, _ in expected.fields if name not in actual_fields)
        if not transforms and not missing and not has_constraints(expected):
            return None
        return (1, expected, transforms, missing, None, None)
    if expected.item is not None and actual.item is not None:
        converter = _conversion(actual.item, expected.item)
        if converter is None and not has_constraints(expected):
            return None
        return (2, expected, (), (), converter, None)
    if expected.choices and actual.choices != expected.choices:
        return (0, expected, (), (), None, expected)
    return None
