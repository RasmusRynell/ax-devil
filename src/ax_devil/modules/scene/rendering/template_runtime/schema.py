"""Generate structural JSON Schema from the language's authoritative definitions."""

from __future__ import annotations

from typing import Any

from ax_devil.modules.scene.rendering.visibility import OverlayFeature

from .definitions import OPERATIONS, PRIMITIVES, TYPES


def catalog_schema() -> dict[str, Any]:
    """Return the catalog's structural JSON Schema; compilation adds semantic validation."""
    expression = {"$ref": "#/$defs/expression"}
    calls = []
    for operation in OPERATIONS.values():
        optional = {name for name, _ in operation.defaults}
        arguments = {name: expression for name, _ in operation.arguments}
        calls.append(
            _object(
                {
                    "call": {"const": operation.name},
                    "args": _object(arguments, [name for name in arguments if name not in optional]),
                },
                ["call", "args"],
            )
        )
    reference = _object({"ref": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 2}}, ["ref"])
    ordinary = {
        "type": "object",
        "propertyNames": {"not": {"enum": ["ref", "call", "literal"]}},
        "additionalProperties": expression,
    }
    steps = []
    for name, fields in PRIMITIVES.items():
        steps.append(
            _object(
                {
                    "primitive": {"const": name},
                    "feature": {"enum": [feature.value for feature in OverlayFeature]},
                    "enabled": expression,
                    "visible": {"type": "boolean"},
                    "label": {"type": "string"},
                    "fields": _object({key: expression for key, _ in fields.fields}, [key for key, _ in fields.fields]),
                    "style": expression,
                },
                ["primitive", "fields"],
            )
        )
    steps.append(
        _object(
            {
                "template": {"type": "string", "minLength": 1},
                "feature": {"enum": [feature.value for feature in OverlayFeature]},
                "enabled": expression,
                "visible": {"type": "boolean"},
                "label": {"type": "string"},
                "inputs": {"type": "object", "additionalProperties": expression},
            },
            ["template"],
        )
    )
    parameter = _object(
        {
            "type": {"enum": list(TYPES)},
            "required": {"type": "boolean"},
            "nullable": {"type": "boolean"},
            "default": {},
            "description": {"type": "string"},
            "label": {"type": "string"},
            "minimum": {"type": "number"},
            "exclusive_minimum": {"type": "number"},
            "maximum": {"type": "number"},
            "max_length": {"type": "integer", "minimum": 0},
        },
        ["type"],
    )
    program = {
        "values": {"type": "object", "additionalProperties": expression},
        "steps": {"type": "array", "items": {"$ref": "#/$defs/step"}},
        "enabled": expression,
        "description": {"type": "string"},
        "label": {"type": "string"},
    }
    template = _object(
        {
            **program,
            "feature": {"enum": [feature.value for feature in OverlayFeature]},
            "parameters": {"type": "object", "additionalProperties": parameter},
        },
        ["steps"],
    )
    bindings = {
        "type": "object",
        "additionalProperties": _object(
            {
                "source": {"const": "primary_classification_attribute"},
                "attribute": {"type": "string", "minLength": 1},
                "value_shape": {"enum": ["bool", "number", "color_classification_list"]},
                "picker": {"enum": ["value", "highest_score"]},
            },
            ["source", "attribute", "value_shape", "picker"],
        ),
    }
    groups: dict[str, Any] = {}
    for group, kinds in (
        ("fallbacks", ["classified", "unclassified"]),
        ("classifications", ["classification"]),
        ("relations", ["relation"]),
    ):
        selector_fields: dict[str, Any] = {"kind": {"enum": kinds}}
        if group != "fallbacks":
            selector_fields["types"] = {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "uniqueItems": True,
            }
        recipe = _object(
            {
                **program,
                "id": {"type": "string", "minLength": 1},
                "bindings": bindings,
                "selector": _object(selector_fields, list(selector_fields)),
            },
            ["id", "selector", "steps"],
        )
        groups[group] = {"type": "array", "items": recipe}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_object(
            {
                "metadata": _object(
                    {
                        "id": {"type": "string", "minLength": 1},
                        "name": {"type": "string", "minLength": 1},
                        "schema_version": {"const": 3},
                        "description": {"type": "string"},
                        "design_notes": {"type": "object"},
                    },
                    ["id", "name", "schema_version"],
                ),
                "templates": {"type": "object", "additionalProperties": template},
                "recipes": _object(groups, ["fallbacks", "classifications"]),
            },
            ["metadata", "templates", "recipes"],
        ),
        "$defs": {
            "functionName": {"enum": sorted(OPERATIONS)},
            # Types, reserved keys, and operation/primitive names make these alternatives
            # mutually exclusive. anyOf can stop at the match instead of revisiting
            # nested expressions to rule out every other alternative.
            "step": {"anyOf": steps},
            "expression": {
                "anyOf": [
                    {"type": ["null", "boolean", "number", "string"]},
                    {"type": "array", "items": expression},
                    reference,
                    _object({"literal": {}}, ["literal"]),
                    ordinary,
                    *calls,
                ]
            },
        },
    }


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
