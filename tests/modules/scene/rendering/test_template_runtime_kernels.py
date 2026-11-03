"""Executable v3 value operations at render time: text, colors, geometry and arithmetic of compiled programs."""

from __future__ import annotations

import pytest

from ax_devil.modules.scene.rendering.template_runtime.program import RenderCatalog
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.catalog_helpers import call_expr as _call
from tests.catalog_helpers import compile_main_template as _compile
from tests.catalog_helpers import main_point_x as _x
from tests.catalog_helpers import point_step as _point
from tests.catalog_helpers import ref_expr as _ref
from tests.drawing_helpers import CONTEXT, BoxCall, PointCall, TextCall, record_template

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
    args: dict[str, object] = {
        "text": _ref("parameters", "text"),
        "max_length": 10,
        "delimiter": None,
        "suffix": "…",
    }
    if elide is not None:
        args["elide"] = elide
    catalog = _compile(
        {
            "parameters": {"text": {"type": "string", "default": ""}},
            "steps": [
                {
                    "primitive": "text",
                    "fields": {
                        "position": {"x": 0, "y": 0},
                        "text": _call("trim_text", **args),
                        "anchor": "baseline",
                    },
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


@pytest.mark.parametrize(
    "name,value",
    [
        ("number", 1),
        ("number", True),
        ("image_box", {"x": 0, "y": 0, "w": 1, "h": 1}),
        ("image_box", {"x": 0, "y": 0, "w": -1, "h": 1}),
        ("image_box", {"x": 0, "y": 0, "w": 1, "h": 1, "extra": 1}),
        ("image_box", {"x": 0, "y": 0, "w": 1}),
        ("image_box", None),
        ("image_box", []),
        ("image_point_list", [{"x": 0, "y": 0}]),
        ("image_point_list", [{"x": True, "y": 0}]),
        ("image_point_list", [{"x": 0, "y": 0}] * 4097),
        ("rgb", [0, 128, 256]),
        ("length", {"value": 1, "unit": "invalid"}),
    ],
)
def test_compiled_boundary_checks_preserve_validation(name: str, value: object) -> None:
    from ax_devil.modules.scene.rendering.template_runtime.definitions import TYPES
    from ax_devil.modules.scene.rendering.template_runtime.validation import compile_validator, validate_value

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
