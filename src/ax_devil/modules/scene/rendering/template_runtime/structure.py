"""Shape checks on raw catalog definitions: objects, keys, lists, enabled-guards and parameter declarations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from .definitions import PARAMETER_BOUNDS, TYPES, ValueType
from .validation import bounded
from .values import TemplateRuntimeError

TemplateDefinition = Mapping[str, object]
Guards = frozenset[tuple[str, ...]]


def require_object(value: object, location: str) -> Mapping[str, object]:
    """Return *value* as a mapping with string keys, or raise ``invalid_structure`` at *location*."""
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TemplateRuntimeError("Expected an object.", code="invalid_structure", location=location)
    return cast(Mapping[str, object], value)


def require_keys(value: Mapping[str, object], allowed: set[str], required: set[str], location: str) -> None:
    """Raise ``invalid_structure`` at *location* unless the keys of *value* include *required* and stay in *allowed*."""
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown or missing:
        raise TemplateRuntimeError(
            f"Unknown fields {sorted(unknown)}; missing fields {sorted(missing)}.",
            code="invalid_structure",
            location=location,
        )


def require_list(value: object, location: str) -> Sequence[object]:
    """Return *value* as a list or tuple, or raise ``invalid_structure`` at *location*."""
    if not isinstance(value, (tuple, list)):
        raise TemplateRuntimeError("Expected an array.", code="invalid_structure", location=location)
    return value


def guard(value: object) -> Guards:
    """Return the reference path that a condition proves present, or no paths.

    Only a bare ``is_present`` or ``is_not_empty`` call on a ``ref`` proves anything.
    """
    if not isinstance(value, Mapping) or value.get("call") not in ("is_present", "is_not_empty"):
        return frozenset()
    args = value.get("args")
    if not isinstance(args, Mapping):
        return frozenset()
    reference = args.get("value")
    if not isinstance(reference, Mapping) or set(reference) != {"ref"}:
        return frozenset()
    path = reference["ref"]
    if not isinstance(path, (tuple, list)) or not all(isinstance(item, str) for item in path):
        return frozenset()
    return frozenset({tuple(path)})


def parameter_type(parameter: Mapping[str, object], location: str) -> ValueType:
    """Return the value type a template parameter declaration accepts, checking the declaration's shape.

    A required parameter has no default; every other parameter declares a literal default.
    """
    require_keys(
        parameter,
        {"type", "required", "nullable", "default", "description", "label", *PARAMETER_BOUNDS},
        {"type"},
        location,
    )
    kind = TYPES.get(cast(str, parameter["type"]))
    if kind is None:
        raise TemplateRuntimeError(f"Unknown parameter type {parameter['type']}.", location=location)
    kind = bounded(kind, {name: parameter[name] for name in PARAMETER_BOUNDS if name in parameter}, location)
    required, nullable = parameter.get("required", False), parameter.get("nullable", False)
    if not isinstance(required, bool) or not isinstance(nullable, bool):
        raise TemplateRuntimeError("required and nullable must be booleans.", location=location)
    if required and "default" in parameter:
        raise TemplateRuntimeError("Required parameters cannot have defaults.", location=location)
    if not required and "default" not in parameter:
        raise TemplateRuntimeError("Optional parameters require a literal default.", location=location)
    return kind.optional() if nullable else kind
