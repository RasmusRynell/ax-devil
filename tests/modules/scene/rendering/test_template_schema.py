"""The distributed authoring schema must match the backend definitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATHS
from ax_devil.modules.scene.rendering.template_runtime.definitions import OPERATIONS
from ax_devil.modules.scene.rendering.template_runtime.schema import catalog_schema

DEFINITIONS = Path("src/ax_devil/modules/scene/rendering/catalog_definitions")


def test_packaged_schema_matches_authoritative_definitions() -> None:
    schema = catalog_schema()
    assert json.loads((DEFINITIONS / "catalog.schema.json").read_text()) == schema
    assert schema["$defs"]["functionName"]["enum"] == sorted(OPERATIONS)


@pytest.mark.parametrize("path", BUILT_IN_CATALOG_PATHS, ids=lambda path: path.stem)
def test_built_in_catalogs_match_generated_schema(path: Path) -> None:
    Draft202012Validator(catalog_schema()).validate(json.loads(path.read_text()))


def test_schema_accepts_nested_expression_forms() -> None:
    """Accept nested values, references, calls, and quoted reserved keys."""
    schema = catalog_schema()
    validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/expression"})
    validator.validate(
        {
            "items": [None, True, 2, "text", {"ref": ["values", "x"]}],
            "ratio": {"call": "div", "args": {"numerator": {"ref": ["values", "x"]}, "denominator": 2}},
            "quoted": {"literal": {"call": "not an expression"}},
        }
    )


@pytest.mark.parametrize(
    "expression",
    [
        {"call": "missing", "args": {}},
        {"call": "div", "args": {"numerator": 1}},
        {"ref": ["values", "x"], "extra": 2},
        {"literal": 1, "ref": ["values", "x"]},
        {"call": "div", "args": {"numerator": 1, "denominator": 2}, "literal": 1},
        {"nested": [{"call": "missing", "args": {}}]},
    ],
)
def test_schema_rejects_invalid_expression_structure(expression: object) -> None:
    schema = catalog_schema()
    validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/expression"})
    with pytest.raises(ValidationError):
        validator.validate(expression)


@pytest.mark.parametrize(
    "step",
    [
        {"primitive": "missing", "fields": {}},
        {"primitive": "point", "fields": {}},
        {"primitive": "point", "fields": {"position": {"x": 0, "y": 0}}, "template": "marker"},
        {"template": "marker", "inputs": {"position": {"call": "missing", "args": {}}}},
    ],
)
def test_schema_rejects_invalid_step_structure(step: object) -> None:
    """Reject unknown, incomplete, mixed, and recursively malformed steps."""
    schema = catalog_schema()
    validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/step"})
    with pytest.raises(ValidationError):
        validator.validate(step)
