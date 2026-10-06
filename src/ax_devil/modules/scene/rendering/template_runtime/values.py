"""Located diagnostics and immutable values for the catalog language."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

Record = Mapping[str, object]
Values = Sequence[object]


@dataclass(frozen=True, slots=True)
class CatalogDiagnostic:
    """A stable error location shared by validation, preview and rendering."""

    code: str
    location: str
    message: str
    recipe_id: str | None = None


class TemplateRuntimeError(ValueError):
    """Carry a located catalog diagnostic across the public loading boundary."""

    def __init__(self, message: str, *, code: str = "invalid_value", location: str = "$") -> None:
        self.diagnostic = CatalogDiagnostic(code, location, message)
        super().__init__(f"{location}: {message}")


def record(value: object) -> Mapping[str, object]:
    """Read a record already established by the compiler or boundary validator."""
    return cast(Record, value)


def sequence(value: object) -> Sequence[object]:
    """Read an immutable collection established by validation."""
    return cast(Values, value)


def number_value(value: object, key: str = "number") -> float:
    """Validate finite numeric boundary data, excluding booleans."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TemplateRuntimeError(f"{key} must be a number.")
    try:
        result = float(value)
    except OverflowError as exc:
        raise TemplateRuntimeError(f"{key} must be finite.") from exc
    if not math.isfinite(result):
        raise TemplateRuntimeError(f"{key} must be finite.")
    return result


def freeze(value: object) -> object:
    """Copy JSON values into an immutable snapshot, validating finite numbers."""
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        number_value(value)
    return value
