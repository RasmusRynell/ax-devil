"""Executable v3 authoring contracts: compiling, scoping, specialization and rejection of catalog programs."""

from __future__ import annotations

from typing import Any

import pytest

from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.catalog_helpers import call_expr as _call
from tests.catalog_helpers import compile_main_template as _compile
from tests.catalog_helpers import main_point_x as _x
from tests.catalog_helpers import point_step as _point
from tests.catalog_helpers import ref_expr as _ref
from tests.drawing_helpers import CONTEXT, PointCall, TextCall, record_template


def test_reordered_calculations_use_dependencies_and_separate_parameters() -> None:
    program: dict[str, Any] = {
        "parameters": {"x": {"type": "number", "default": 0.25}},
        "values": {
            "result": _call("add", values=[_ref("values", "x"), 0.25]),
            "x": _ref("parameters", "x"),
        },
        "steps": [_point(_ref("values", "result"))],
    }
    assert _x(_compile(program)) == 0.5
    program["values"] = dict(reversed(list(program["values"].items())))
    assert _x(_compile(program), {"x": 0.5}) == 0.75


@pytest.mark.parametrize("value", [0, False, ""])
def test_coalesce_preserves_false_zero_and_empty_text(value: object) -> None:
    kind = "bool" if isinstance(value, bool) else "number" if isinstance(value, int) else "string"
    program = {
        "parameters": {"input": {"type": kind, "nullable": True, "default": None}},
        "values": {"selected": _call("coalesce", values=[_ref("parameters", "input"), value])},
        "steps": [_point(enabled=_call("is_present", value=_ref("values", "selected")))],
    }
    assert len(record_template(_compile(program), "main", {"input": value}, context=CONTEXT)) == 1


def test_nested_calls_have_independent_scopes_and_keep_drawing_order() -> None:
    child = {"parameters": {"x": {"type": "number", "required": True}}, "steps": [_point(_ref("parameters", "x"))]}
    root = {"steps": [{"template": "child", "inputs": {"x": 0.3}}, {"template": "child", "inputs": {"x": 0.7}}]}
    output = record_template(_compile(root, child=child), "main", {}, context=CONTEXT)
    assert [getattr(item, "x") for item in output] == [0.3, 0.7]


@pytest.mark.parametrize(
    "step",
    [
        _point(_call("if", condition=True, then=0.5, **{"else": _call("div", numerator=1, denominator=0)})),
        _point(_call("coalesce", values=[0.5, _call("div", numerator=1, denominator=0)])),
    ],
)
def test_lazy_expressions_do_not_evaluate_unused_arithmetic(step: dict[str, object]) -> None:
    assert _x(_compile({"steps": [step]})) == 0.5


def test_disabled_steps_and_unused_values_do_not_evaluate_failing_calculations() -> None:
    catalog = _compile(
        {
            "values": {
                "bad": _call("div", numerator=1, denominator=0),
                "unused": _call("number_text", value=3, precision=-1, hide_zero=False),
            },
            "steps": [_point(_ref("values", "bad"), enabled=False), _point(0.2)],
        }
    )
    assert _x(catalog) == 0.2


def test_disabled_recipe_does_not_evaluate_values_or_steps() -> None:
    catalog = _compile(
        {
            "enabled": False,
            "values": {"bad": _call("div", numerator=1, denominator=0)},
            "steps": [_point(_ref("values", "bad"))],
        }
    )
    assert record_template(catalog, "main", {}, context=CONTEXT) == []


@pytest.mark.parametrize(
    "expression",
    [
        _ref("values", "missing"),
        _call("div", numerator=1),
        _call("div", numerator="wrong", denominator=2),
        _call("if", condition=True, then=1, **{"else": _call("missing")}),
        {"ref": ["parameters", "x"], "extra": 1},
        _call("add", values=[True, 1]),
        _call("mul", values=[{"value": 2, "unit": "px"}, {"value": 3, "unit": "px"}]),
        _call("div", numerator=1, denominator={"value": 2, "unit": "px"}),
    ],
)
def test_compile_rejects_invalid_expressions_even_in_disabled_steps(expression: object) -> None:
    with pytest.raises(TemplateRuntimeError):
        _compile({"steps": [_point(expression, enabled=False)]})


def test_calculated_value_cycles_are_rejected() -> None:
    with pytest.raises(TemplateRuntimeError, match="cycle"):
        _compile({"values": {"a": _ref("values", "b"), "b": _ref("values", "a")}, "steps": []})


def test_recursive_templates_are_rejected() -> None:
    with pytest.raises(TemplateRuntimeError, match="Recursive"):
        _compile({"steps": [{"template": "main"}]})


def test_expansion_limit_counts_every_call_site_without_materializing_it() -> None:
    templates: dict[str, object] = {"leaf": {"steps": [_point()]}}
    previous = "leaf"
    for index in range(15):
        name = f"level{index}"
        templates[name] = {"steps": [{"template": previous}, {"template": previous}]}
        previous = name
    with pytest.raises(TemplateRuntimeError, match="limit"):
        _compile({"steps": [{"template": previous}]}, **templates)


def test_nullable_parameter_requires_a_guard_before_nonnullable_use() -> None:
    program = {
        "parameters": {"x": {"type": "number", "default": None, "nullable": True}},
        "steps": [_point(_ref("parameters", "x"))],
    }
    with pytest.raises(TemplateRuntimeError, match="Expected"):
        _compile(program)
    program["steps"] = [_point(_ref("parameters", "x"), enabled=_call("is_present", value=_ref("parameters", "x")))]
    catalog = _compile(program)
    assert record_template(catalog, "main", {}, context=CONTEXT) == []
    assert _x(catalog, {"x": 0}) == 0


def test_required_nullable_parameter_must_still_be_supplied() -> None:
    child = {"parameters": {"x": {"type": "number", "required": True, "nullable": True}}, "steps": []}
    with pytest.raises(TemplateRuntimeError, match="Missing required"):
        _compile({"steps": [{"template": "child"}]}, child=child)


def test_template_inputs_reject_unknown_fields_and_do_not_inherit_caller_values() -> None:
    child = {"steps": [_point(_ref("values", "caller"))]}
    with pytest.raises(TemplateRuntimeError, match="Unknown"):
        _compile({"values": {"caller": 1}, "steps": [{"template": "child"}]}, child=child)
    with pytest.raises(TemplateRuntimeError):
        _compile({"steps": [{"template": "child", "inputs": {"extra": 1}}]}, child={"steps": []})


def test_compiled_catalog_is_detached_from_later_document_edits() -> None:
    default = {"x": 0.3, "y": 0.4}
    program: dict[str, Any] = {
        "parameters": {"origin": {"type": "image_point", "default": default}},
        "steps": [_point(_ref("parameters", "origin", "x")), _point(0.25)],
    }
    catalog = _compile(program)
    default["x"] = 0.8
    program["steps"][1]["fields"]["position"]["x"] = 0.75
    output = record_template(catalog, "main", {}, context=CONTEXT)
    assert [point.x for point in output if isinstance(point, PointCall)] == [0.3, 0.25]


def test_literal_dollar_text_is_not_a_reference() -> None:
    catalog = _compile(
        {
            "steps": [
                {
                    "primitive": "text",
                    "fields": {"position": {"x": 0, "y": 0}, "text": "$values.price", "anchor": "baseline"},
                    "style": {"text": {"color": [255, 255, 255], "size": {"value": 12, "unit": "px"}}},
                }
            ]
        }
    )
    text = record_template(catalog, "main", {}, context=CONTEXT)[0]
    assert isinstance(text, TextCall)
    assert text.text == "$values.price"


def test_reference_reads_a_field_of_a_calculated_record() -> None:
    assert (
        _x(_compile({"values": {"origin": {"x": 0.7, "y": 0.2}}, "steps": [_point(_ref("values", "origin", "x"))]}))
        == 0.7
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_literals_are_rejected(value: float) -> None:
    with pytest.raises(TemplateRuntimeError, match="finite"):
        _compile({"steps": [_point(value)]})


def test_invalid_color_stop_order_is_rejected_even_in_unused_branch() -> None:
    expression = _call(
        "color_ramp", value=0.5, stops=[{"at": 1, "color": [0, 0, 0]}, {"at": 0, "color": [255, 255, 255]}]
    )
    with pytest.raises(TemplateRuntimeError, match="increasing"):
        _compile({"values": {"unused": expression}, "steps": []})


def test_guarded_parent_makes_its_required_descendants_accessible() -> None:
    # image_arrow itself has no nullable children: a parent guard allows its nested endpoints.
    program = {
        "parameters": {"shape": {"type": "image_arrow", "default": None, "nullable": True}},
        "steps": [
            _point(
                _ref("parameters", "shape", "start", "x"),
                enabled=_call("is_present", value=_ref("parameters", "shape")),
            )
        ],
    }
    catalog = _compile(program)
    assert record_template(catalog, "main", {}, context=CONTEXT) == []
    assert (
        _x(catalog, {"shape": {"start": {"x": 0.4, "y": 0.2}, "end": {"x": 0.7, "y": 0.3}, "head_points": []}}) == 0.4
    )


@pytest.mark.parametrize(
    "style",
    [
        {"stroke": {"color": [255, 0, 0], "width": {"value": -1, "unit": "px"}, "pattern": "solid"}},
        {"fill": {"color": [255, 0, 0], "alpha": 256}},
        {"fill": {"color": [256, 0, 0], "alpha": 255}},
    ],
)
def test_constant_style_constraints_are_validated_before_activation(style: object) -> None:
    with pytest.raises(TemplateRuntimeError):
        _compile(
            {"steps": [{"primitive": "box", "fields": {"geometry": {"x": 0, "y": 0, "w": 1, "h": 1}}, "style": style}]}
        )


def test_literal_reserved_key_record_can_be_referenced_without_becoming_code() -> None:
    assert (
        _x(
            _compile(
                {
                    "values": {"payload": {"literal": {"call": 0.4, "ref": "data"}}},
                    "steps": [_point(_ref("values", "payload", "call"))],
                }
            )
        )
        == 0.4
    )


def test_specialized_template_keeps_null_input_behind_its_guard() -> None:
    child = {
        "parameters": {"x": {"type": "number", "nullable": True, "default": None}},
        "steps": [_point(_ref("parameters", "x"), enabled=_call("is_present", value=_ref("parameters", "x")))],
    }
    catalog = _compile({"steps": [{"template": "child"}, {"template": "child", "inputs": {"x": 0.4}}]}, child=child)
    assert _x(catalog) == 0.4


def test_specialization_does_not_evaluate_guarded_failure_at_compile_time() -> None:
    child = {
        "parameters": {"show": {"type": "bool", "required": True}},
        "enabled": _ref("parameters", "show"),
        "values": {"bad": _call("div", numerator=1, denominator=0)},
        "steps": [_point(_ref("values", "bad"))],
    }
    hidden = _compile({"steps": [{"template": "child", "inputs": {"show": False}}]}, child=child)
    assert record_template(hidden, "main", {}, context=CONTEXT) == []
    shown = _compile({"steps": [{"template": "child", "inputs": {"show": True}}]}, child=child)
    with pytest.raises(TemplateRuntimeError, match="divide by zero"):
        record_template(shown, "main", {}, context=CONTEXT)


def test_context_dependent_values_follow_the_render_target_and_stay_lazy() -> None:
    from tests.drawing_helpers import RecordingTarget

    catalog = _compile(
        {
            "parameters": {"show": {"type": "bool", "default": False}},
            "values": {
                "position": _call(
                    "offset_point",
                    point={"x": _ref("context", "px_h"), "y": 0},
                    dx={"value": 8, "unit": "px"},
                    dy={"value": 0, "unit": "px"},
                )
            },
            "steps": [_point(_ref("values", "position", "x"), enabled=_ref("parameters", "show"))],
        }
    )
    program = catalog.programs["main"]
    hidden = RecordingTarget()
    program.emit({"parameters": {"show": False}}, CONTEXT, hidden)
    assert hidden.calls == []
    for context in (CONTEXT, RenderContext.create(1600, 800), CONTEXT):
        target = RecordingTarget()
        program.emit({"parameters": {"show": True}}, context, target)
        point = target.calls[0]
        assert isinstance(point, PointCall)
        assert point.x == 9 / context.width


def test_scene_values_are_read_and_validated_per_invocation() -> None:
    from ax_devil.modules.scene.rendering.template_runtime.definitions import SCENE
    from tests.drawing_helpers import RecordingTarget

    program = RenderProgramCompiler().compile_program(
        {"steps": [_point(_ref("scene", "observation", "geometry", "x")) for _ in range(2)]},
        roots={"scene": SCENE},
    )
    for x in (0.2, 0.4):
        target = RecordingTarget()
        program.emit({"scene": {"observation": {"geometry": {"x": x}}}}, CONTEXT, target)
        assert [point.x for point in target.calls if isinstance(point, PointCall)] == [x, x]
    with pytest.raises(TemplateRuntimeError, match="finite"):
        program.emit({"scene": {"observation": {"geometry": {"x": float("nan")}}}}, CONTEXT, RecordingTarget())


def test_specialization_distinguishes_signed_zero_constants() -> None:
    child = {
        "parameters": {"value": {"type": "number", "required": True}},
        "steps": [
            {
                "primitive": "text",
                "fields": {
                    "position": {"x": 0, "y": 0},
                    "anchor": "baseline",
                    "text": _call(
                        "coalesce",
                        values=[
                            _call("number_text", value=_ref("parameters", "value"), precision=1, hide_zero=False),
                            "",
                        ],
                    ),
                },
                "style": {"text": {"color": [0, 0, 0], "size": {"value": 10, "unit": "px"}}},
            }
        ],
    }
    catalog = _compile(
        {"steps": [{"template": "child", "inputs": {"value": value}} for value in (0.0, -0.0)]}, child=child
    )
    output = record_template(catalog, "main", {}, context=CONTEXT)
    assert [item.text for item in output if isinstance(item, TextCall)] == ["0.0", "-0.0"]
