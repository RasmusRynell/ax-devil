"""Scene projection and routing of compiled catalogs: recipe selection, bindings, relations and inputs."""

from __future__ import annotations

from functools import lru_cache

import pytest
from pytest import MonkeyPatch

from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    BoundingBox,
    Classification,
    ColorClassification,
    Entity,
    EntityId,
    EntityRelation,
    ImageVelocity,
    MotionState,
    NormalizedPoint,
    Observation,
    Polygon,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalog
from ax_devil.modules.scene.rendering.catalog_loader import SceneRenderCatalogLoader
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.catalog_helpers import call_expr as _call
from tests.catalog_helpers import catalog_document
from tests.catalog_helpers import observed_scene as _scene
from tests.catalog_helpers import ref_expr as _ref
from tests.drawing_helpers import (
    BoxCall,
    DrawCalls,
    LineCall,
    PointCall,
    PolygonCall,
    TextCall,
    record_scene,
)


@lru_cache(maxsize=None)
def _runtime_catalog(visibility: OverlayVisibility = OverlayVisibility()) -> SceneRenderCatalog:
    """Exercise scene projection with explicit test inputs, independent of built-in appearance."""
    document = catalog_document()
    geometry = _ref("scene", "observation", "geometry")
    position = {"x": 0, "y": 0}
    bindings = {
        "color": {
            "source": "primary_classification_attribute",
            "attribute": "colors",
            "value_shape": "color_classification_list",
            "picker": "highest_score",
        },
        "flag": {
            "source": "primary_classification_attribute",
            "attribute": "flag",
            "value_shape": "bool",
            "picker": "value",
        },
        "amount": {
            "source": "primary_classification_attribute",
            "attribute": "amount",
            "value_shape": "number",
            "picker": "value",
        },
    }
    badge = _ref("scene", "entity", "motion_state")
    velocity = _ref("scene", "observation", "velocity")
    steps = [
        {"primitive": "box", "fields": {"geometry": geometry}},
        {
            "primitive": "polygon",
            "enabled": _call("is_present", value=_ref("scene", "observation", "polygon_points")),
            "fields": {"points": _ref("scene", "observation", "polygon_points")},
        },
        {
            "primitive": "text",
            "fields": {"position": position, "text": _ref("scene", "entity", "id"), "anchor": "baseline"},
        },
        {
            "primitive": "text",
            "fields": {
                "position": position,
                "text": _call("coalesce", values=[_ref("scene", "classification", "type"), "unclassified"]),
                "anchor": "baseline",
            },
        },
        {
            "primitive": "box",
            "enabled": _call("is_present", value=_ref("bindings", "color")),
            "fields": {"geometry": geometry},
            "style": {"fill": {"color": _ref("bindings", "color", "color"), "alpha": 255}},
        },
        {
            "primitive": "point",
            "enabled": _call("coalesce", values=[_ref("bindings", "flag"), False]),
            "fields": {"position": {"x": 0.9, "y": 0}},
        },
        {
            "primitive": "point",
            "enabled": _call("is_present", value=_ref("bindings", "amount")),
            "fields": {"position": {"x": _ref("bindings", "amount"), "y": 0}},
        },
        {
            "primitive": "text",
            "feature": "movement",
            "enabled": _call("is_present", value=badge),
            "fields": {"position": position, "text": _call("coalesce", values=[badge, ""]), "anchor": "baseline"},
        },
        {
            "primitive": "line",
            "feature": "speed",
            "enabled": _call("is_present", value=velocity),
            "fields": {
                "start": position,
                "end": {
                    "x": _call("coalesce", values=[_ref("scene", "observation", "velocity", "vx"), 0]),
                    "y": _call("coalesce", values=[_ref("scene", "observation", "velocity", "vy"), 0]),
                },
            },
        },
    ]
    for step in steps:
        if step["primitive"] == "text":
            step["style"] = {"text": {"color": [255, 255, 255], "size": {"value": 12, "unit": "px"}}}
    for recipe in document["recipes"]["fallbacks"] + document["recipes"]["classifications"]:
        recipe.update(bindings=bindings, steps=steps)
    relation = document["recipes"]["relations"][0]
    relation["steps"] = [
        {
            "primitive": "line",
            "fields": {
                "start": _call(
                    "box_anchor", geometry=_ref("scene", "source", "observation", "geometry"), anchor="center"
                ),
                "end": _call(
                    "box_anchor", geometry=_ref("scene", "target", "observation", "geometry"), anchor="center"
                ),
            },
        }
    ]
    return SceneRenderCatalogLoader().validate_revision(document).specialize(visibility)


def _render(scene: Scene, *, visibility: OverlayVisibility = OverlayVisibility()) -> DrawCalls:
    return record_scene(_runtime_catalog(visibility), scene, RenderContext.create(640, 480))


@pytest.mark.parametrize(
    "types,recipe_id",
    [
        ((), "motion"),
        (("custom",), "generic_classified"),
        (("human",), "human"),
        (("car",), "vehicle"),
        (("vehicle",), "vehicle"),
        (("custom", "human"), "human"),
    ],
)
def test_routing_uses_primary_classification_and_catalog_selectors(types: tuple[str, ...], recipe_id: str) -> None:
    """Fallbacks and shared selectors route by the highest scoring classification."""
    scene = _scene()
    entity = next(iter(scene.entities.values()))
    entity.observations[0].classification = [
        Classification(
            type=kind,
            score=Score((index + 1) / len(types)),
            confidence=Score((len(types) - index) / len(types)),
        )
        for index, kind in enumerate(types)
    ]
    recipe = _runtime_catalog().recipe_for_entity(entity)
    assert recipe is not None and recipe.recipe_id == recipe_id
    texts = [item.text for item in _render(scene) if isinstance(item, TextCall)]
    assert texts == ["entity", types[-1] if types else "unclassified"]


def test_latest_observation_projects_geometry_polygon_and_primary_attributes() -> None:
    """Geometry and bindings come from the latest observation and its best classification."""
    scene = _scene(attributes=[Attribute("amount", 0.1)])
    entity = next(iter(scene.entities.values()))
    entity.add_observation(
        Observation(
            geometry=Polygon(points=[NormalizedPoint(0.5, 0.5), NormalizedPoint(0.7, 0.5), NormalizedPoint(0.7, 0.8)]),
            frame_number=1,
            classification=[
                Classification(type="custom", score=Score(0.2), attributes=[Attribute("amount", 0.2)]),
                Classification(
                    type="human",
                    score=Score(0.9),
                    attributes=[
                        Attribute("amount", 0.7),
                        Attribute(
                            "colors",
                            [
                                ColorClassification("weak", RGB(10, 20, 30), Score(0.1)),
                                ColorClassification("strong", RGB(40, 50, 60), Score(0.9)),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    primitives = _render(scene)
    boxes = [item for item in primitives if isinstance(item, BoxCall)]
    assert len(boxes) == 2
    for box in boxes:
        assert (box.x, box.y, box.w, box.h) == pytest.approx((0.5, 0.5, 0.2, 0.3))
    assert (boxes[1].style.brush_r, boxes[1].style.brush_g, boxes[1].style.brush_b) == (40, 50, 60)
    assert [item.x for item in primitives if isinstance(item, PointCall)] == [0.7]
    assert [item.points for item in primitives if isinstance(item, PolygonCall)] == [
        ((0.5, 0.5), (0.7, 0.5), (0.7, 0.8))
    ]


@pytest.mark.parametrize(
    "attributes,points,has_color",
    [
        ([], [], False),
        ([Attribute("flag", True)], [0.9], False),
        ([Attribute("flag", False)], [], False),
        ([Attribute("flag", "true")], [], False),
        ([Attribute("amount", 0.0)], [0.0], False),
        ([Attribute("amount", "0")], [], False),
        ([Attribute("colors", [ColorClassification("blue", RGB(0, 0, 200), Score(0.8))])], [], True),
        ([Attribute("colors", [ColorClassification("blue", RGB(0, 0, 200), Score(0.8)), "invalid"])], [], False),
    ],
)
def test_optional_bindings_keep_valid_false_and_zero_and_ignore_wrong_shapes(
    attributes: list[Attribute], points: list[float], has_color: bool
) -> None:
    primitives = _render(_scene(attributes=attributes))
    assert [item.x for item in primitives if isinstance(item, PointCall)] == points
    assert len([item for item in primitives if isinstance(item, BoxCall)]) == (2 if has_color else 1)
    assert [item.text for item in primitives if isinstance(item, TextCall)] == ["entity", "human"]


@pytest.mark.parametrize("motion_state", [MotionState.Moving, MotionState.Stationary, None])
@pytest.mark.parametrize("visible", [True, False])
def test_movement_and_speed_inputs_respect_visibility(motion_state: MotionState | None, visible: bool) -> None:
    scene = _scene(motion_state=motion_state)
    entity = next(iter(scene.entities.values()))
    entity.observations[0].velocity_in_image_space = ImageVelocity(vx=0.2, vy=0.1)
    primitives = _render(
        scene,
        visibility=OverlayVisibility()
        .with_feature(OverlayFeature.MOVEMENT, visible)
        .with_feature(OverlayFeature.SPEED, visible),
    )
    badges = [
        item.text
        for item in primitives
        if isinstance(item, TextCall) and item.text in tuple(state.value for state in MotionState)
    ]
    assert badges == ([motion_state.value] if visible and motion_state is not None else [])
    lines = [item for item in primitives if isinstance(item, LineCall)]
    assert [(line.x2, line.y2) for line in lines] == ([(0.2, 0.1)] if visible else [])


def test_has_part_relation_draws_line_between_entity_centers() -> None:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    human = Entity(id=EntityId("37"))
    human.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.125, 0.25, 0.25, 0.5),
            classification=[Classification(type="human", score=Score(0.9))],
            frame_number=1,
        )
    )
    head = Entity(id=EntityId("38"))
    head.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.5, 0.125, 0.125, 0.125),
            classification=[Classification(type="head", score=Score(0.9))],
            frame_number=1,
        )
    )
    scene.add_entity(human)
    scene.add_entity(head)
    scene.add_relation(
        EntityRelation(
            type="has_part",
            source_entity_id=human.id,
            target_entity_id=head.id,
        )
    )

    relation_lines = [primitive for primitive in _render(scene) if isinstance(primitive, LineCall)]

    assert len(relation_lines) == 1
    assert (relation_lines[0].x1, relation_lines[0].y1) == (0.25, 0.5)
    assert (relation_lines[0].x2, relation_lines[0].y2) == (0.5625, 0.1875)


def test_relation_is_not_drawn_when_an_endpoint_is_not_in_scene() -> None:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    human = Entity(id=EntityId("37"))
    human.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.125, 0.25, 0.25, 0.5),
            classification=[Classification(type="human", score=Score(0.9))],
            frame_number=1,
        )
    )
    scene.add_entity(human)
    scene.add_relation(
        EntityRelation(
            type="has_part",
            source_entity_id=human.id,
            target_entity_id=EntityId("38"),
        )
    )

    assert not any(isinstance(primitive, LineCall) for primitive in _render(scene))


def test_relations_render_in_stable_semantic_order() -> None:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    for entity_id, x in (("source", 0.1), ("target-b", 0.5), ("target-a", 0.3)):
        entity = Entity(id=EntityId(entity_id))
        entity.add_observation(
            Observation(
                geometry=BoundingBox.from_xywh(x, 0.1, 0.1, 0.1),
                classification=[Classification(type="human", score=Score(0.9))],
                frame_number=1,
            )
        )
        scene.add_entity(entity)
    scene.add_relation(
        EntityRelation(
            type="has_part",
            source_entity_id=EntityId("source"),
            target_entity_id=EntityId("target-b"),
        )
    )
    scene.add_relation(
        EntityRelation(
            type="has_part",
            source_entity_id=EntityId("source"),
            target_entity_id=EntityId("target-a"),
        )
    )

    relation_lines = [primitive for primitive in _render(scene) if isinstance(primitive, LineCall)]

    assert [line.x2 for line in relation_lines] == [0.35, 0.55]


def test_compiled_projection_reads_geometry_once_and_skips_unused_polygon(monkeypatch: MonkeyPatch) -> None:
    """Only demanded Scene fields are read, once per entity per frame, shared by every step."""
    from ax_devil.modules.scene.rendering.catalog import CompiledSceneRenderRecipe
    from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
    from ax_devil.modules.scene.rendering.template_runtime.definitions import SCENE
    from tests.drawing_helpers import RecordingTarget

    reads = 0
    original = BoundingBox.as_xywh

    def geometry(box: BoundingBox) -> tuple[float, float, float, float]:
        nonlocal reads
        reads += 1
        return original(box)

    def polygon(box: BoundingBox) -> None:
        raise AssertionError("Unused polygon data must not be projected")

    monkeypatch.setattr(BoundingBox, "as_xywh", geometry)
    monkeypatch.setattr(BoundingBox, "polygon_points", polygon)
    step = {
        "primitive": "box",
        "fields": {"geometry": {"ref": ["scene", "observation", "geometry"]}},
        "style": {"fill": {"color": [255, 0, 0], "alpha": 255}},
    }
    program = RenderProgramCompiler().compile_program({"steps": [step, step]}, roots={"scene": SCENE})
    recipe = CompiledSceneRenderRecipe("projection", program, {})
    target = RecordingTarget()
    rows = [
        (
            Entity(id=EntityId(f"object-{index}")),
            Observation(geometry=BoundingBox.from_xywh(index * 0.1, 0.2, 0.3, 0.4), frame_number=index),
            None,
        )
        for index in range(2)
    ]
    recipe.render_rows(rows, RenderContext.create(800, 400), target)
    assert reads == 2
    assert [call.x for call in target.calls if isinstance(call, BoxCall)] == [0, 0.1, 0, 0.1]
