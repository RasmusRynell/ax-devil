"""Value bounds are declared on value types, exported to the language reference and enforced at every check."""

from __future__ import annotations

from functools import partial
from typing import Any

import pytest

from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
from ax_devil.modules.scene.rendering.template_runtime.definitions import (
    TYPES,
    authoring_definitions,
    compile_validator,
    validate_value,
)
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from tests.drawing_helpers import CONTEXT, BoxCall, record_template


def test_authoring_definitions_export_bounds_with_json_schema_names() -> None:
    definitions: Any = authoring_definitions()

    rgb = definitions["types"]["rgb"]
    assert (rgb["minItems"], rgb["maxItems"]) == (3, 3)
    assert (rgb["items"]["minimum"], rgb["items"]["maximum"]) == (0, 255)
    box = definitions["types"]["image_box"]["fields"]
    assert box["w"]["minimum"] == 0 and "minimum" not in box["x"]
    style = definitions["primitives"]["box"]["style"]["fields"]
    assert style["fill"]["fields"]["alpha"]["maximum"] == 255
    assert style["stroke"]["fields"]["width"]["fields"]["value"]["minimum"] == 0
    text = definitions["primitives"]["text"]["style"]["fields"]["text"]["fields"]
    assert text["size"]["fields"]["value"]["exclusiveMinimum"] == 0


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("rgb", [0, 256, 0], "rgb item must be at least 0 and at most 255."),
        ("rgb", [0, 0], "rgb must be a list of exactly 3 items."),
        ("image_box", {"x": -1, "y": 0, "w": -0.5, "h": 1}, "w must be at least 0."),
        ("shape_style", {"fill": {"color": [0, 0, 0], "alpha": -1}}, "alpha must be at least 0 and at most 255."),
    ],
)
def test_values_outside_their_bounds_are_rejected_by_field(name: str, value: object, message: str) -> None:
    for validate in (partial(validate_value, expected=TYPES[name]), compile_validator(TYPES[name])):
        with pytest.raises(TemplateRuntimeError) as error:
            validate(value)
        assert error.value.diagnostic.message == message


def test_values_on_their_bounds_are_accepted() -> None:
    assert validate_value([0, 255, 0], TYPES["rgb"]) == (0, 255, 0)
    assert validate_value({"x": -1, "y": 2, "w": 0, "h": 0}, TYPES["image_box"])["w"] == 0  # type: ignore[index]


def _styled_box(alpha: object, width: object) -> dict[str, Any]:
    return {
        "primitive": "box",
        "fields": {"geometry": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}},
        "style": {
            "stroke": {"color": [255, 0, 0], "width": {"value": width, "unit": "px"}, "pattern": "solid"},
            "fill": {"color": [255, 0, 0], "alpha": alpha},
        },
    }


def _template(alpha: object, width: object) -> dict[str, Any]:
    return {
        "parameters": {"alpha": {"type": "integer", "default": 10}, "width": {"type": "number", "default": 1}},
        "steps": [_styled_box(alpha, width)],
    }


def test_row_values_are_checked_against_bounds_in_shared_and_row_evaluation() -> None:
    catalog = RenderProgramCompiler().compile_catalog(
        {"main": _template({"ref": ["parameters", "alpha"]}, {"ref": ["parameters", "width"]})}
    )

    assert isinstance(record_template(catalog, "main", {"alpha": 255, "width": 0}, context=CONTEXT)[0], BoxCall)
    with pytest.raises(TemplateRuntimeError, match="alpha must be at least 0 and at most 255"):
        record_template(catalog, "main", {"alpha": 256}, context=CONTEXT)
    with pytest.raises(TemplateRuntimeError, match="length must be at least 0"):
        record_template(catalog, "main", {"width": -1}, context=CONTEXT)


def test_constant_values_outside_bounds_fail_compilation_at_their_location() -> None:
    with pytest.raises(TemplateRuntimeError) as error:
        RenderProgramCompiler().compile_catalog({"main": _template(300, 1)})

    assert error.value.diagnostic.message == "alpha must be at least 0 and at most 255."
    assert error.value.diagnostic.location.startswith("$.templates.main.steps[0].style")


def test_parameter_default_outside_bounds_is_located_at_the_default() -> None:
    template = {"parameters": {"color": {"type": "rgb", "default": [0, 0, 300]}}, "steps": []}

    with pytest.raises(TemplateRuntimeError) as error:
        RenderProgramCompiler().compile_catalog({"main": template})

    assert error.value.diagnostic.location == "$.templates.main.parameters.color.default"
    assert error.value.diagnostic.message == "color item must be at least 0 and at most 255."


def _bounded_template(declaration: dict[str, Any], argument: object) -> dict[str, Any]:
    return {
        "main": {
            "parameters": {"size": {"type": "length", "required": True}},
            "steps": [{"template": "label", "inputs": {"size": argument}}],
        },
        "label": {
            "parameters": {"size": declaration},
            "steps": [
                {
                    "primitive": "text",
                    "fields": {"position": {"x": 0.5, "y": 0.5}, "text": "x", "anchor": "center"},
                    "style": {"text": {"color": [255, 255, 255], "size": {"ref": ["parameters", "size"]}}},
                }
            ],
        },
    }


def test_parameter_bounds_reject_constant_inputs_at_the_input() -> None:
    declaration = {"type": "length", "exclusive_minimum": 2, "maximum": 40, "default": {"value": 8, "unit": "px"}}

    with pytest.raises(TemplateRuntimeError) as error:
        RenderProgramCompiler().compile_catalog(_bounded_template(declaration, {"value": 1, "unit": "px"}))

    assert error.value.diagnostic.location == "$.templates.main.steps[0].inputs.size"
    assert error.value.diagnostic.message == "length must be greater than 2 and at most 40."


def test_parameter_bounds_check_inputs_that_vary_per_row() -> None:
    declaration = {"type": "length", "minimum": 4, "default": {"value": 8, "unit": "px"}}
    catalog = RenderProgramCompiler().compile_catalog(_bounded_template(declaration, {"ref": ["parameters", "size"]}))

    assert record_template(catalog, "main", {"size": {"value": 4, "unit": "px"}}, context=CONTEXT)
    with pytest.raises(TemplateRuntimeError, match="length must be at least 4"):
        record_template(catalog, "main", {"size": {"value": 3, "unit": "px"}}, context=CONTEXT)


def test_bounded_number_parameters_check_inputs_that_vary_per_row() -> None:
    template = {
        "main": {
            "parameters": {"gain": {"type": "number", "required": True}},
            "steps": [{"template": "dot", "inputs": {"gain": {"ref": ["parameters", "gain"]}}}],
        },
        "dot": {
            "parameters": {"gain": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5}},
            "steps": [{"primitive": "point", "fields": {"position": {"x": {"ref": ["parameters", "gain"]}, "y": 0}}}],
        },
    }
    catalog = RenderProgramCompiler().compile_catalog(template)

    assert record_template(catalog, "main", {"gain": 1}, context=CONTEXT)
    with pytest.raises(TemplateRuntimeError, match="gain must be at least 0 and at most 1"):
        record_template(catalog, "main", {"gain": 1.5}, context=CONTEXT)


def test_only_numbers_text_and_lengths_declare_bounds() -> None:
    template = {"main": {"parameters": {"color": {"type": "rgb", "minimum": 0, "default": [0, 0, 0]}}, "steps": []}}

    with pytest.raises(TemplateRuntimeError, match="rgb parameter cannot declare minimum"):
        RenderProgramCompiler().compile_catalog(template)
