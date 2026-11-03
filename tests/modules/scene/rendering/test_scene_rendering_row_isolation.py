"""Failed or absent rows stay isolated: valid objects in a batch still draw."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    EntityRelation,
    MotionState,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering.catalog_loader import SceneRenderCatalogLoader
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.catalog_helpers import call_expr as _call
from tests.catalog_helpers import catalog_document
from tests.catalog_helpers import ref_expr as _ref
from tests.drawing_helpers import (
    PointCall,
    TextCall,
    record_scene,
)


@pytest.mark.parametrize(
    "colors,color",
    [
        (
            [[255, 0, 0]],
            lambda palette: _call("pick_color", text=_ref("scene", "entity", "id"), colors=palette),
        ),
        ([{"at": 0, "color": [255, 0, 0]}], lambda stops: _call("color_ramp", value=0, stops=stops)),
    ],
    ids=["pick_color", "color_ramp"],
)
def test_guarded_palette_keeps_valid_objects_in_a_mixed_batch(
    colors: list[object], color: Callable[[dict[str, object]], dict[str, object]]
) -> None:
    """A palette missing on an inactive row must not suppress another object's label."""
    document = catalog_document()
    human = next(recipe for recipe in document["recipes"]["classifications"] if recipe["id"] == "human")
    palette = _ref("values", "palette")
    human["values"] = {
        "palette": _call(
            "if",
            condition=_call(
                "gt", left=_call("coalesce", values=[_ref("scene", "classification", "score"), 0]), right=0.5
            ),
            then=colors,
            **{"else": None},
        )
    }
    human["steps"] = [
        {
            "primitive": "text",
            "enabled": _call("is_present", value=palette),
            "fields": {"position": {"x": 0, "y": 0}, "text": _ref("scene", "entity", "id"), "anchor": "baseline"},
            "style": {
                "text": {
                    "color": color(palette),
                    "size": {"value": 12, "unit": "px"},
                }
            },
        }
    ]
    catalog = SceneRenderCatalogLoader().validate_document(document)
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    for object_id, score in (("hidden", 0.1), ("visible", 0.9)):
        entity = Entity(id=EntityId(object_id))
        entity.add_observation(
            Observation(
                geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2),
                frame_number=0,
                classification=[Classification(type="human", score=Score(score))],
            )
        )
        scene.add_entity(entity)
    diagnostics: list[CatalogDiagnostic] = []
    output = record_scene(catalog, scene, RenderContext.create(640, 480), diagnostics=diagnostics)
    assert diagnostics == []
    assert len(output) == 1
    label = output[0]
    assert isinstance(label, TextCall)
    assert label.text == "visible"
    assert (label.style.pen_r, label.style.pen_g, label.style.pen_b) == (255, 0, 0)


def test_lookup_text_keeps_absent_rows_absent_and_catalog_strings_as_data() -> None:
    """Batched lookup maps present rows, leaves absent rows absent and never interprets catalog strings as code."""
    from ax_devil.modules.scene.rendering.catalog import CompiledSceneRenderRecipe
    from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
    from ax_devil.modules.scene.rendering.template_runtime.definitions import SCENE
    from tests.drawing_helpers import RecordingTarget

    motion = _ref("scene", "entity", "motion_state")
    program = RenderProgramCompiler().compile_program(
        {
            "steps": [
                {
                    "primitive": "text",
                    "enabled": _call("is_present", value=motion),
                    "fields": {
                        "position": {"x": 0, "y": 0},
                        "anchor": "baseline",
                        "text": _call(
                            "lookup_text",
                            text=motion,
                            mapping=[{"key": "moving", "text": "custom 'symbol'"}],
                            default="fallback",
                        ),
                    },
                    "style": {"text": {"color": [255, 255, 255], "size": {"value": 12, "unit": "px"}}},
                }
            ],
        },
        roots={"scene": SCENE},
    )
    recipe = CompiledSceneRenderRecipe("lookup", program, {})
    rows = [
        (
            Entity(id=EntityId(f"object-{index}"), motion_state=state),
            Observation(geometry=BoundingBox.from_xywh(0, 0, 0.1, 0.1), frame_number=index),
            None,
        )
        for index, state in enumerate((MotionState.Moving, None, MotionState.Unknown, MotionState.Stationary))
    ]
    for selected in (rows, rows[1:2], []):
        target = RecordingTarget()
        recipe.render_rows(selected, RenderContext.create(800, 400), target)
        expected = [
            "custom 'symbol'" if entity.motion_state is MotionState.Moving else "fallback"
            for entity, _, _ in selected
            if entity.motion_state is not None
        ]
        assert [item.text for item in target.calls if isinstance(item, TextCall)] == expected


@pytest.mark.parametrize("field,kind", [("size", "length"), ("runs", "label_runs")])
def test_labels_skip_guarded_null_rows_without_losing_present_rows(field: str, kind: str) -> None:
    """A disabled label with missing data must not prevent its neighbors from rendering."""
    from ax_devil.modules.scene.rendering.catalog import CompiledSceneRenderRecipe
    from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
    from ax_devil.modules.scene.rendering.template_runtime.definitions import SCENE
    from tests.drawing_helpers import LabelCall, RecordingTarget

    size = {"value": 12, "unit": "px"}
    zero = {"value": 0, "unit": "px"}
    runs = [{"text": "Present", "color": [255, 255, 255], "weight": "regular"}]
    compiler = RenderProgramCompiler()
    compiler.compile_catalog(
        {
            "label": {
                "parameters": {field: {"type": kind, "nullable": True, "required": True}},
                "steps": [
                    {
                        "primitive": "label",
                        "enabled": _call("is_present", value=_ref("parameters", field)),
                        "fields": {
                            "position": {"x": 0, "y": 0},
                            "anchor": "top-left",
                            "runs": _ref("parameters", field) if field == "runs" else runs,
                        },
                        "style": {
                            "label": {
                                "size": _ref("parameters", field) if field == "size" else size,
                                "padding_x": zero,
                                "padding_y": zero,
                                "radius": zero,
                                "gap": zero,
                            }
                        },
                    }
                ],
            }
        }
    )
    program = compiler.compile_program(
        {
            "steps": [
                {
                    "template": "label",
                    "inputs": {
                        field: _call(
                            "if",
                            condition=_call("is_present", value=_ref("scene", "entity", "motion_state")),
                            then=size if field == "size" else runs,
                            **{"else": None},
                        )
                    },
                }
            ]
        },
        roots={"scene": SCENE},
    )
    recipe = CompiledSceneRenderRecipe("guarded_label", program, {})
    rows = [
        (
            Entity(id=EntityId(f"object-{index}"), motion_state=state),
            Observation(geometry=BoundingBox.from_xywh(0, 0, 0.1, 0.1), frame_number=index),
            None,
        )
        for index, state in enumerate((MotionState.Moving, None, MotionState.Stationary))
    ]
    context = RenderContext.create(800, 400)
    target = RecordingTarget()
    recipe.render_rows(rows, context, target)
    individual = RecordingTarget()
    for row in rows:
        recipe.render_rows([row], context, individual)
    assert target.calls == individual.calls
    assert len(target.calls) == 2
    assert all(isinstance(call, LabelCall) and call.content.runs[0].text == "Present" for call in target.calls)


@pytest.mark.parametrize("relation_recipe", [False, True])
def test_runtime_failure_discards_only_the_affected_recipe_output(relation_recipe: bool) -> None:
    document = catalog_document()
    recipe = document["recipes"]["relations" if relation_recipe else "fallbacks"][0]
    geometry_path = (
        ["scene", "source", "observation", "geometry", "w"]
        if relation_recipe
        else ["scene", "observation", "geometry", "w"]
    )
    recipe["steps"] = [
        {"primitive": "point", "fields": {"position": {"x": 0.5, "y": 0}}},
        {
            "primitive": "point",
            "fields": {
                "position": {
                    "x": {
                        "call": "div",
                        "args": {"numerator": 1, "denominator": {"ref": geometry_path}},
                    },
                    "y": 0,
                }
            },
        },
    ]
    if relation_recipe:
        document["recipes"]["fallbacks"][0]["steps"] = []
    catalog = SceneRenderCatalogLoader().validate_document(document)
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    for name, width in (("bad", 0.0), ("good", 0.5)):
        entity = Entity(id=EntityId(name))
        entity.add_observation(Observation(geometry=BoundingBox.from_xywh(0, 0, width, 0.2), frame_number=0))
        scene.add_entity(entity)
        if relation_recipe:
            scene.add_relation(EntityRelation(type="has_part", source_entity_id=entity.id, target_entity_id=entity.id))
    diagnostics: list[CatalogDiagnostic] = []
    primitives = record_scene(catalog, scene, RenderContext.create(800, 400), diagnostics=diagnostics)
    assert len(primitives) == 2
    assert all(isinstance(item, PointCall) for item in primitives)
    assert len(diagnostics) == 1
    assert "steps[1]" in diagnostics[0].location
    assert "zero" in diagnostics[0].message
    assert diagnostics[0].recipe_id == recipe["id"]
    assert [item.x for item in primitives if isinstance(item, PointCall)] == [0.5, 2.0]
