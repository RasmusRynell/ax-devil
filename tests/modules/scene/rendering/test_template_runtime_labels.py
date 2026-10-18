"""Executable v3 labels and box styling: style resolution, runs, bars and located overflow errors."""

from __future__ import annotations

from typing import Any, cast

import pytest

from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from tests.catalog_helpers import compile_main_template as _compile
from tests.catalog_helpers import px_length as _px
from tests.catalog_helpers import ref_expr as _ref
from tests.drawing_helpers import CONTEXT, BoxCall, LabelCall, record_template


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
