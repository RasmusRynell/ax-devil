"""Executable v3 authoring contracts, independent of Qt painting and file storage."""

from __future__ import annotations

from typing import Any, cast

import pytest

from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
from ax_devil.modules.scene.rendering.template_runtime.program import RenderCatalog
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.drawing_helpers import BoxCall, LabelCall, PointCall, TextCall, record_template

CONTEXT = RenderContext.create(800, 400)


def _ref(*parts: str) -> dict[str, object]:
    return {"ref": list(parts)}


def _call(name: str, **args: object) -> dict[str, object]:
    return {"call": name, "args": args}


def _point(x: object = 0, *, enabled: object = True) -> dict[str, object]:
    return {
        "primitive": "point",
        "enabled": enabled,
        "fields": {"position": {"x": x, "y": 0}},
        "style": {"stroke": {"color": [255, 255, 255], "width": {"value": 1, "unit": "px"}, "pattern": "solid"}},
    }


def _compile(program: dict[str, Any], **templates: object) -> RenderCatalog:
    return RenderProgramCompiler().compile_catalog({"main": program, **templates})


def _x(catalog: RenderCatalog, inputs: dict[str, object] | None = None) -> float:
    point = record_template(catalog, "main", inputs or {}, context=CONTEXT)[0]
    assert isinstance(point, PointCall)
    return point.x


def test_reordered_calculations_use_dependencies_and_separate_parameters() -> None:
    program: dict[str, Any] = {
        "parameters": {"x": {"type": "number", "default": 0.25}},
        "values": {"result": _call("add", values=[_ref("values", "x"), 0.25]), "x": _ref("parameters", "x")},
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


_LOOKUPS = [("moving", ["▶"]), ("still", [""]), ("", ["empty"]), ("Moving", ["?"])]
_MOTION_SYMBOLS = [{"key": "moving", "text": "▶"}, {"key": "still", "text": ""}, {"key": "", "text": "empty"}]


def _lookup_text_catalog(text: object, mapping: object) -> RenderCatalog:
    lookup = _call("lookup_text", text=text, mapping=mapping, default="?")
    return _compile(
        {
            "parameters": {
                "text": {"type": "string", "nullable": True, "default": None},
                "plain": {"type": "bool", "default": True},
            },
            "steps": [
                {
                    "primitive": "text",
                    "enabled": _call("is_present", value=text),
                    "fields": {"position": {"x": 0, "y": 0}, "text": lookup, "anchor": "baseline"},
                    "style": {"text": {"color": [255, 255, 255], "size": {"value": 12, "unit": "px"}}},
                }
            ],
        }
    )


@pytest.mark.parametrize(
    "text,expected,constant",
    [
        *((text, expected, constant) for text, expected in _LOOKUPS for constant in (False, True)),
        (None, [], False),
    ],
)
def test_lookup_text_maps_matches_defaults_unmatched_and_keeps_absent_text_absent(
    text: str | None, expected: list[str], constant: bool
) -> None:
    """Constant folding and runtime lookup agree, including empty matched values."""
    catalog = _lookup_text_catalog(text if constant else _ref("parameters", "text"), _MOTION_SYMBOLS)
    calls = record_template(catalog, "main", {"text": text}, context=CONTEXT)
    assert [item.text for item in calls if isinstance(item, TextCall)] == expected


@pytest.mark.parametrize(
    "text,elide,expected",
    [
        ("3f2a9c1e-7b4d-4e8a-9c2f-1a2b3c4d5e6f", None, "3f2a9c1e-7…"),
        ("3f2a9c1e-7b4d-4e8a-9c2f-1a2b3c4d5e6f", "end", "3f2a9c1e-7…"),
        ("3f2a9c1e-7b4d-4e8a-9c2f-1a2b3c4d5e6f", "middle", "3f2a9…d5e6f"),
        ("1042", "middle", "1042"),
    ],
)
def test_trim_text_elides_at_the_end_by_default_or_in_the_middle(text: str, elide: str | None, expected: str) -> None:
    args: dict[str, object] = {"text": _ref("parameters", "text"), "max_length": 10, "delimiter": None, "suffix": "…"}
    if elide is not None:
        args["elide"] = elide
    catalog = _compile(
        {
            "parameters": {"text": {"type": "string", "default": ""}},
            "steps": [
                {
                    "primitive": "text",
                    "fields": {"position": {"x": 0, "y": 0}, "text": _call("trim_text", **args), "anchor": "baseline"},
                    "style": {"text": {"color": [255, 255, 255], "size": {"value": 12, "unit": "px"}}},
                }
            ],
        }
    )
    calls = record_template(catalog, "main", {"text": text}, context=CONTEXT)
    assert [item.text for item in calls if isinstance(item, TextCall)] == [expected]


@pytest.mark.parametrize("plain,expected", [(True, "▶"), (False, "moving")])
def test_lookup_text_accepts_a_mapping_chosen_per_row(plain: bool, expected: str) -> None:
    mapping = _call(
        "if",
        condition=_ref("parameters", "plain"),
        then=_MOTION_SYMBOLS,
        **{"else": [{"key": "moving", "text": "moving"}]},
    )
    catalog = _lookup_text_catalog(_ref("parameters", "text"), mapping)
    calls = record_template(catalog, "main", {"text": "moving", "plain": plain}, context=CONTEXT)
    assert [item.text for item in calls if isinstance(item, TextCall)] == [expected]


@pytest.mark.parametrize(
    "mapping",
    [{"moving": "▶"}, [{"key": "moving", "text": 1}], [{"key": "moving"}], [{"key": "a", "text": "1"}] * 2],
)
def test_lookup_text_rejects_invalid_mappings(mapping: object) -> None:
    with pytest.raises(TemplateRuntimeError):
        _lookup_text_catalog("moving", mapping)


_PALETTE = [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 0], [0, 255, 255]]


def _picked_colors(texts: list[str], text: object) -> list[tuple[int, int, int]]:
    catalog = _compile(
        {
            "parameters": {"text": {"type": "string", "default": ""}},
            "steps": [
                {
                    "primitive": "text",
                    "fields": {"position": {"x": 0, "y": 0}, "text": "x", "anchor": "baseline"},
                    "style": {
                        "text": {
                            "color": _call("pick_color", text=text, colors=_PALETTE),
                            "size": {"value": 12, "unit": "px"},
                        }
                    },
                }
            ],
        }
    )
    colors: list[tuple[int, int, int]] = []
    for value in texts:
        call = record_template(catalog, "main", {"text": value}, context=CONTEXT)[0]
        assert isinstance(call, TextCall)
        colors.append((call.style.pen_r, call.style.pen_g, call.style.pen_b))
    return colors


def test_pick_color_gives_each_text_a_stable_palette_color() -> None:
    """The same id gets the same color whether constant or per row, and ids spread over the palette."""
    ids = [str(number) for number in range(40)]
    colors = _picked_colors(ids, _ref("parameters", "text"))
    assert colors == _picked_colors(ids, _ref("parameters", "text"))
    assert _picked_colors(["7"], "7") == [colors[7]]
    assert set(colors) == {(red, green, blue) for red, green, blue in _PALETTE}


def test_reference_reads_a_field_of_a_calculated_record() -> None:
    assert (
        _x(_compile({"values": {"origin": {"x": 0.7, "y": 0.2}}, "steps": [_point(_ref("values", "origin", "x"))]}))
        == 0.7
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_literals_are_rejected(value: float) -> None:
    with pytest.raises(TemplateRuntimeError, match="finite"):
        _compile({"steps": [_point(value)]})


def test_finite_operands_cannot_produce_an_infinite_primitive() -> None:
    catalog = _compile(
        {
            "parameters": {"x": {"type": "number", "required": True}},
            "steps": [_point(_call("mul", values=[_ref("parameters", "x"), 1e300]))],
        }
    )
    with pytest.raises(TemplateRuntimeError, match="finite") as error:
        record_template(catalog, "main", {"x": 1e300}, context=CONTEXT)
    assert "fields" in error.value.diagnostic.location


def test_length_ratio_uses_the_target_dimensions() -> None:
    catalog = _compile(
        {
            "steps": [
                _point(
                    _call("div", numerator={"value": 20, "unit": "px"}, denominator={"value": 1, "unit": "image_width"})
                )
            ]
        }
    )
    assert _x(catalog) == 20 / 800
    point = record_template(catalog, "main", {}, context=RenderContext.create(400, 800))[0]
    assert isinstance(point, PointCall)
    assert point.x == 20 / 400


def test_inset_collapses_oversized_margin_at_midpoint() -> None:
    catalog = _compile(
        {
            "values": {
                "box": _call(
                    "inset_box", geometry={"x": 0.2, "y": 0.3, "w": 0.4, "h": 0.2}, margin={"value": 1000, "unit": "px"}
                )
            },
            "steps": [
                _point(_ref("values", "box", "x")),
                {"primitive": "box", "fields": {"geometry": _ref("values", "box")}},
            ],
        }
    )
    assert _x(catalog) == pytest.approx(0.4)
    assert len(record_template(catalog, "main", {}, context=CONTEXT)) == 1


def test_every_primitive_kind_draws() -> None:
    position = {"x": 0.1, "y": 0.2}
    points = [position, {"x": 0.5, "y": 0.2}, {"x": 0.4, "y": 0.6}]
    fields = {
        "box": {"geometry": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}},
        "circle": {"center": position, "radius": {"value": 8, "unit": "px"}},
        "point": {"position": position},
        "line": {"start": position, "end": points[1]},
        "polyline": {"points": points},
        "polygon": {"points": points},
        "text": {"position": position, "text": "Example", "anchor": "baseline"},
    }
    steps = [
        {
            "primitive": name,
            "fields": value,
            "style": {"text": {"color": [0, 0, 0], "size": {"value": 8, "unit": "px"}}} if name == "text" else {},
        }
        for name, value in fields.items()
    ]
    assert len(record_template(_compile({"steps": steps}), "main", {}, context=CONTEXT)) == 7


def test_outside_image_geometry_is_preserved() -> None:
    catalog = _compile(
        {"steps": [{"primitive": "box", "fields": {"geometry": {"x": -0.2, "y": 1.2, "w": 0.5, "h": 0.2}}}]}
    )
    box = record_template(catalog, "main", {}, context=CONTEXT)[0]
    assert isinstance(box, BoxCall)
    assert box.x == -0.2 and box.y == 1.2


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


@pytest.mark.parametrize(
    "name,value",
    [
        ("number", 1),
        ("number", True),
        ("image_box", {"x": 0, "y": 0, "w": 1, "h": 1}),
        ("image_box", {"x": 0, "y": 0, "w": -1, "h": 1}),
        ("image_box", None),
        ("image_point_list", [{"x": 0, "y": 0}]),
        ("image_point_list", [{"x": 0, "y": 0}] * 4097),
        ("rgb", [0, 128, 256]),
        ("length", {"value": 1, "unit": "invalid"}),
    ],
)
def test_compiled_boundary_checks_preserve_validation(name: str, value: object) -> None:
    from ax_devil.modules.scene.rendering.template_runtime.definitions import TYPES, compile_validator, validate_value

    for kind in (TYPES[name], TYPES[name].optional()):
        try:
            expected = validate_value(value, kind)
        except TemplateRuntimeError as expected_error:
            with pytest.raises(TemplateRuntimeError) as actual_error:
                compile_validator(kind)(value)
            assert actual_error.value.diagnostic == expected_error.diagnostic
        else:
            assert compile_validator(kind)(value) == expected


def test_compiled_arithmetic_preserves_integer_comparisons_and_adds_left_to_right() -> None:
    """Shared-input lowering must not round integer guards, and adds left to right like per-row columns.

    Per-row table columns are float64, so the integer case is checked for shared evaluation only. Python 3.12+
    ``sum()`` compensates float rounding and NumPy does not, so shared and per-row results agree only with plain ``+``.
    """
    value = _ref("parameters", "value")
    increased = _call("add", values=[value, 1])
    catalog = _compile(
        {
            "parameters": {"value": {"type": "number", "required": True}},
            "steps": [_point(0.5, enabled=_call("gt", left=increased, right=value))],
        }
    )
    point = record_template(catalog, "main", {"value": 2**53}, context=CONTEXT, rows=False)[0]
    assert isinstance(point, PointCall) and point.x == 0.5
    summed = _compile(
        {
            "parameters": {"value": {"type": "number", "required": True}},
            "steps": [_point(_call("add", values=[value, 1, -1e16]))],
        }
    )
    assert _x(summed, {"value": 1e16}) == (1e16 + 1) + -1e16


def test_compiled_circle_overflow_keeps_its_step_diagnostic() -> None:
    """Finite source lengths may overflow when resolved against the render target."""
    catalog = _compile(
        {
            "steps": [
                {
                    "primitive": "circle",
                    "fields": {
                        "center": {"x": 0.5, "y": 0.5},
                        "radius": {"value": 1e308, "unit": "image_width"},
                    },
                }
            ]
        }
    )
    with pytest.raises(TemplateRuntimeError, match="finite") as error:
        record_template(catalog, "main", {}, context=CONTEXT)
    assert error.value.diagnostic.location == "$.templates.main.steps[0]"


def test_generated_update_treats_parameter_names_as_data() -> None:
    """Catalog-controlled strings never become executable Python identifiers or source."""
    name = "x']; raise RuntimeError('injected') #\n\u03bb"
    catalog = _compile(
        {
            "parameters": {name: {"type": "number", "required": True}},
            "steps": [_point(_ref("parameters", name))],
        }
    )
    assert _x(catalog, {name: 0.375}) == 0.375


def _px(value: float) -> dict[str, object]:
    return {"value": value, "unit": "px"}


def _label_step(runs: list[dict[str, object]]) -> dict[str, object]:
    return {
        "primitive": "label",
        "fields": {"position": {"x": 0.25, "y": 0.5}, "anchor": "bottom-left", "runs": runs},
        "style": {
            "label": {
                "size": _px(12),
                "family": None,
                "background": {"color": [14, 16, 20], "alpha": 205},
                "padding_x": _px(8),
                "padding_y": _px(3),
                "radius": {"value": 0.5, "unit": "image_min"},
                "gap": _px(5),
            }
        },
    }


def test_label_resolves_its_style_and_keeps_only_runs_with_text() -> None:
    program = {
        "parameters": {
            "id": {"type": "string", "default": "3f2a9c1e"},
            "note": {"type": "string", "nullable": True, "default": None},
        },
        "steps": [
            _label_step(
                [
                    {"text": "Person", "color": [255, 255, 255], "weight": "semibold"},
                    {"text": _ref("parameters", "id"), "color": [185, 192, 200], "weight": "regular"},
                    {"text": _ref("parameters", "note"), "color": [185, 192, 200], "weight": "regular"},
                    {"text": "", "color": [185, 192, 200], "weight": "regular"},
                ]
            )
        ],
    }
    label = record_template(_compile(program), "main", {}, context=CONTEXT)[0]
    assert isinstance(label, LabelCall)
    assert (label.x, label.y, label.anchor) == (0.25, 0.5, "bottom-left")
    content = label.content
    assert [(run.text, run.color, run.weight) for run in content.runs] == [
        ("Person", (255, 255, 255), "semibold"),
        ("3f2a9c1e", (185, 192, 200), "regular"),
    ]
    scale = CONTEXT.scale_factor
    sizes = (content.size, content.padding_x, content.padding_y, content.gap)
    assert sizes == (12 / scale, 8 / scale, 3 / scale, 5 / scale)
    assert content.radius == 0.5
    assert (content.family, content.background) == ("", (14, 16, 20, 205))


@pytest.mark.parametrize("score,filled", [(0.874, 0.87), (1.5, 1.0), (-0.2, 0.0)])
def test_label_bars_mix_with_text_runs_and_are_kept_to_hundredths_within_zero_and_one(
    score: float, filled: float
) -> None:
    program = {
        "parameters": {"score": {"type": "number", "default": 0}},
        "steps": [
            _label_step(
                [
                    {"bar": _ref("parameters", "score"), "color": [255, 176, 32], "weight": "regular"},
                    {"text": "score", "color": [185, 192, 200], "weight": "regular"},
                ]
            )
        ],
    }
    label = record_template(_compile(program), "main", {"score": score}, context=CONTEXT)[0]
    assert isinstance(label, LabelCall)
    assert [(run.text, run.bar) for run in label.content.runs] == [("", filled), ("score", None)]


def test_label_rejects_unknown_weights() -> None:
    step = _label_step([{"text": "Person", "color": [255, 255, 255], "weight": "heavy"}])
    with pytest.raises(TemplateRuntimeError):
        _compile({"steps": [step]})


@pytest.mark.parametrize("field", ["size", "radius"])
def test_label_length_overflow_reports_a_located_error(field: str) -> None:
    """Finite lengths can overflow during unit conversion and must never reach the painter."""
    step = _label_step([{"text": "Person", "color": [255, 255, 255], "weight": "regular"}])
    label = dict(cast(dict[str, Any], step["style"])["label"])
    label[field] = {"value": 1e308, "unit": "image_width"}
    step["style"] = {"label": label}
    with pytest.raises(TemplateRuntimeError, match="finite") as error:
        record_template(_compile({"steps": [step]}), "main", {}, context=CONTEXT)
    assert error.value.diagnostic.location.startswith("$.templates.main.steps[0]")


def test_box_corner_radius_reaches_the_paint_and_defaults_to_square() -> None:
    def box(style: dict[str, object]) -> dict[str, object]:
        return {"primitive": "box", "fields": {"geometry": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}, "style": style}

    fill = {"color": [255, 255, 255], "alpha": 255}
    rounded, square = record_template(
        _compile({"steps": [box({"fill": fill, "radius": _px(6)}), box({"fill": fill})]}), "main", {}, context=CONTEXT
    )
    assert isinstance(rounded, BoxCall) and isinstance(square, BoxCall)
    assert (rounded.style.radius, square.style.radius) == (6 / CONTEXT.scale_factor, 0.0)
