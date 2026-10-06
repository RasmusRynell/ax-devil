"""A compact, always-current reference of the render catalog language, printed by ``ax-devil catalog reference``.

It is generated from the runtime's own definitions, so the names, types and bounds it lists are exactly what the
compiler accepts. An agent editing a catalog reads it instead of guessing operation or field names.
"""

from __future__ import annotations

import json
from typing import Any

from ax_devil.modules.scene.rendering.catalog import BINDING_TYPES
from ax_devil.modules.scene.rendering.template_runtime.definitions import TYPES, ValueType, authoring_definitions
from ax_devil.modules.scene.rendering.visibility import OverlayFeature

_BOUND_SYMBOLS = (("minimum", ">="), ("exclusive_minimum", ">"), ("maximum", "<="), ("maxLength", "max length"))


def language_reference() -> str:
    """Return the language reference: types, operations, primitives, recipe inputs, bindings and limits."""
    definitions: Any = authoring_definitions()
    lines = [f"Render catalog language, schema_version {definitions['schema_version']}", ""]
    lines.append("TYPES (named types used below; ? means the value may be null)")
    roots = [
        *definitions["types"].values(),
        *(argument for operation in definitions["operations"].values() for argument in operation["arguments"].values()),
        *(part for primitive in definitions["primitives"].values() for part in primitive.values()),
    ]
    named: dict[str, dict[str, Any]] = {}
    for root in roots:
        _collect(root, named)
    lines += [f"  {name} = {_expanded(kind)}" for name, kind in named.items()]
    lines += ["", 'OPERATIONS  {"call": "<name>", "args": {"<argument>": <expression>}}']
    for name, operation in definitions["operations"].items():
        arguments = ", ".join(_argument(key, argument) for key, argument in operation["arguments"].items())
        lines.append(f"  {name}({arguments}) -> {_type(operation['result'])}")
    lines += ["", 'PRIMITIVES  {"primitive": "<name>", "fields": {...}, "style": {...}}']
    for name, primitive in definitions["primitives"].items():
        lines.append(f"  {name}: fields {_expanded(primitive['fields'])}; style {_type(primitive['style'])}")
    lines += ["", 'ENTITY RECIPE INPUTS  {"ref": ["scene", "observation", "geometry"]}']
    lines += _paths("scene", definitions["entity_scene"])
    lines += _paths("context", definitions["context"])
    lines.append("  bindings.<name>: see BINDINGS; values.<name>: the recipe's own calculated values")
    lines += ["", "RELATION RECIPE INPUTS"]
    lines += _paths("scene", definitions["relation_scene"])
    lines.append("  context.*: as for entity recipes")
    lines += ["", "TEMPLATE INPUTS", "  parameters.<name>, values.<name>, context.*"]
    lines += ["", 'BINDINGS  "bindings": {"<name>": {"source": "primary_classification_attribute",']
    lines.append(
        '                                  "attribute": "<detection attribute>", "value_shape": ..., "picker": ...}}'
    )
    for (shape, picker), kind in BINDING_TYPES.items():
        lines.append(f'  value_shape "{shape}", picker "{picker}" -> {_binding(kind)}?')
    lines += ["", 'OVERLAY FEATURES  optional "feature" on templates or steps; view choices gate both']
    lines += [f"  {feature.value}: {feature.label}; {feature.description}" for feature in OverlayFeature]
    lines += ["", "LIMITS"]
    lines += [f"  {name}: {value}" for name, value in definitions["limits"].items()]
    return "\n".join(lines)


def _binding(kind: ValueType) -> str:
    """Return what a binding gives recipes: a plain type, or a record such as a color and its score."""
    if not kind.fields:
        return kind.name
    fields = ", ".join(f"{key}: {field.name}" for key, field in kind.fields)
    return f"{{{fields}}}"


def _collect(kind: dict[str, Any], named: dict[str, dict[str, Any]]) -> None:
    """Add every named type with fields, choices or items inside *kind* to *named*, outermost first."""
    name = str(kind["type"])
    if name != "list" and name not in named and ("fields" in kind or "choices" in kind or "items" in kind):
        named[name] = kind
    for part in (*kind.get("fields", {}).values(), *([kind["items"]] if "items" in kind else [])):
        _collect(part, named)


def _type(kind: dict[str, Any]) -> str:
    """Return a short name for *kind*: its type name, a list of its items, and its bounds."""
    name = str(kind["type"])
    if name == "list" and "items" in kind:
        name = f"list[{_type(kind['items'])}]"
    bounds = " ".join(f"{symbol} {kind[key]}" for key, symbol in _BOUND_SYMBOLS if key in kind)
    return f"{name}{'?' if kind.get('nullable') else ''}{f' ({bounds})' if bounds else ''}"


def _expanded(kind: dict[str, Any]) -> str:
    """Return *kind* written out one level: its fields, choices or items."""
    if "fields" in kind:
        fields = ", ".join(f"{key}: {_type(field)}" for key, field in kind["fields"].items())
        return f"{{{fields}}}"
    if "choices" in kind:
        return " | ".join(f'"{choice}"' for choice in kind["choices"])
    count = f" x{kind['minItems']}" if kind.get("minItems") == kind.get("maxItems") and "minItems" in kind else ""
    return f"[{_type(kind['items'])}]{count}"


def _paths(root: str, kind: dict[str, Any]) -> list[str]:
    """Return every readable path under *root* with its type, one per line."""
    lines = []
    for key, field in kind.get("fields", {}).items():
        path = f"{root}.{key}"
        if "fields" in field and str(field["type"]) not in TYPES:
            lines.append(f"  {path}{'?' if field.get('nullable') else ''}")
            lines += _paths(path, field)
        else:
            lines.append(f"  {path}: {_type(field)}")
    return lines


def _argument(key: str, argument: dict[str, Any]) -> str:
    default = f" = {json.dumps(argument['default'])}" if "default" in argument else ""
    return f"{key}: {_type(argument)}{default}"
