from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from functools import lru_cache
from typing import Any

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
from ax_devil.modules.scene.rendering.catalog import (
    SceneRenderCatalog,
    SceneRenderCatalogLoader,
)
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.catalog_helpers import CLASSIC_CATALOG_PATH, catalog_document, classic_catalog, classic_variant
from tests.catalog_helpers import call_expr as _call
from tests.catalog_helpers import ref_expr as _ref
from tests.drawing_helpers import (
    BoxCall,
    CircleCall,
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


def _render_built_in(scene: Scene) -> DrawCalls:
    return record_scene(classic_catalog(), scene, RenderContext.create(640, 480))


def _scene(*, attributes: list[Attribute] | None = None, motion_state: MotionState | None = None) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("entity"), motion_state=motion_state)
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4),
            frame_number=0,
            classification=[Classification(type="human", score=Score(0.8), attributes=attributes or [])],
        )
    )
    scene.add_entity(entity)
    return scene


def _built_in_human_with(inputs_by_step: dict[str, dict[str, object]]) -> SceneRenderCatalog:
    """Return the built-in catalog with extra inputs on steps of the human recipe, keyed by step label."""
    document: dict[str, Any] = json.loads(CLASSIC_CATALOG_PATH.read_text(encoding="utf-8"))
    human = next(recipe for recipe in document["recipes"]["classifications"] if recipe["id"] == "human")
    for step in human["steps"]:
        step["inputs"].update(inputs_by_step.get(step["label"], {}))
    return SceneRenderCatalogLoader().validate_document(document)


def _human_scene(size_px: tuple[float, float], *, attributes: list[Attribute] | None = None) -> Scene:
    """Return a moving human with a box of *size_px* in a 512×512 view."""
    scene = _scene(motion_state=MotionState.Moving, attributes=attributes)
    observation = next(iter(scene.entities.values())).observations[0]
    observation.geometry = BoundingBox.from_xywh(0.1, 0.2, size_px[0] / 512, size_px[1] / 512)
    return scene


def _record_512(scene: Scene, catalog: SceneRenderCatalog | None = None) -> DrawCalls:
    return record_scene(catalog or classic_catalog(), scene, RenderContext.create(512, 512))


def _fill(item: BoxCall | CircleCall) -> tuple[int, int, int]:
    return (item.style.brush_r, item.style.brush_g, item.style.brush_b)


_RED = RGB(210, 30, 40)
_RED_COLORS = [
    Attribute(attribute, [ColorClassification("red", _RED, Score(0.75))])
    for attribute in ("upper_clothing_colors", "lower_clothing_colors", "vehicle_colors")
]


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
        Classification(type=kind, score=Score((index + 1) / len(types))) for index, kind in enumerate(types)
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


@pytest.mark.parametrize("motion_state", [MotionState.Moving, None])
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


@pytest.mark.parametrize("classification", [None, "custom", "human", "car", "head"])
def test_built_in_recipes_keep_movement_and_speed_controls_independent(classification: str | None) -> None:
    """Each packaged recipe hides its own movement badge and speed arrow, and only that, per control."""
    scene = _scene(motion_state=MotionState.Moving)
    observation = next(iter(scene.entities.values())).observations[0]
    observation.classification = [] if classification is None else [Classification(classification, Score(0.8))]
    observation.velocity_in_image_space = ImageVelocity(vx=0.2, vy=0.1)
    context = RenderContext.create(640, 480)

    def render(*hidden: OverlayFeature) -> Counter[str]:
        return Counter(map(repr, record_scene(classic_variant(OverlayVisibility(frozenset(hidden))), scene, context)))

    shown = render()
    movement = shown - render(OverlayFeature.MOVEMENT)
    speed = shown - render(OverlayFeature.SPEED)
    assert movement and speed and not movement & speed
    assert shown - movement - speed == render(OverlayFeature.MOVEMENT, OverlayFeature.SPEED)


@pytest.mark.parametrize(
    "classification,attribute",
    [("human", "upper_clothing_colors"), ("human", "lower_clothing_colors"), ("car", "vehicle_colors")],
)
def test_built_in_recipes_render_the_best_detected_color(classification: str, attribute: str) -> None:
    """Detected color data reaches packaged output; catalog-defined colors remain free to change."""
    scene = _scene(
        attributes=[
            Attribute(
                attribute,
                [
                    ColorClassification("weak", RGB(11, 22, 33), Score(0.1)),
                    ColorClassification("strong", RGB(44, 55, 66), Score(0.9)),
                ],
            )
        ]
    )
    entity = next(iter(scene.entities.values()))
    entity.observations[0].classification[0].type = classification
    colors = [_fill(item) for item in _render_built_in(scene) if isinstance(item, (BoxCall, CircleCall))]
    assert (44, 55, 66) in colors
    assert (11, 22, 33) not in colors


@pytest.mark.parametrize("classification,wide", [(None, False), ("human", True)])
def test_built_in_indicators_remain_visible_on_small_boxes(classification: str | None, wide: bool) -> None:
    """Classified and unclassified small detections retain their visible indicators."""
    short_side = 26
    size = (256, short_side) if wide else (short_side, 256)
    scene = _human_scene(size, attributes=[Attribute("face_visible", 0.8), *_RED_COLORS])
    observation = next(iter(scene.entities.values())).observations[0]
    if classification is None:
        observation.classification = []
    else:
        observation.classification[0].type = classification
    observation.velocity_in_image_space = ImageVelocity(vx=0.2, vy=0.1)
    calls = _record_512(scene)
    texts = [item.text for item in calls if isinstance(item, TextCall)]
    boxes = [item for item in calls if isinstance(item, BoxCall)]
    assert {"entity", "▶"} <= set(texts)
    assert any(isinstance(item, LineCall) for item in calls)
    assert any((box.w * 512, box.h * 512) == pytest.approx(size) for box in boxes)
    if classification is not None:
        assert classification in texts
        assert len(boxes) >= 2  # The outer box and its confidence fill.


@pytest.mark.parametrize("width,height", [(16, 20), (100, 200)])
def test_built_in_bag_marker_stays_clear_of_clothing_color_dots(width: int, height: int) -> None:
    """The bag marker sits between the upper and lower color dots without overlapping them, even on short boxes."""
    without_bag = _record_512(
        _human_scene((width, height), attributes=[Attribute("carries_bag", False), *_RED_COLORS[:2]])
    )
    calls = _record_512(_human_scene((width, height), attributes=[Attribute("carries_bag", True), *_RED_COLORS[:2]]))
    [bag] = [item for item in calls if isinstance(item, BoxCall) and item not in without_bag]
    upper, lower = sorted((item for item in calls if isinstance(item, CircleCall)), key=lambda dot: dot.cy)
    assert upper.cy + upper.r <= bag.y
    assert bag.y + bag.h <= lower.cy - lower.r


def test_built_in_automatic_indicator_sizes_follow_the_box_while_labels_stay_fixed() -> None:
    """Automatic dot and motion symbol sizes grow with the box and stay inside it; ids and classes do not scale."""
    upper_color = [Attribute("upper_clothing_colors", [ColorClassification("red", _RED, Score(0.75))])]
    diameters, symbol_sizes, label_sizes = [], [], set()
    for side in (8, 26, 60, 200):
        calls = _record_512(_human_scene((side, side * 2), attributes=upper_color))
        dot = next(item for item in calls if isinstance(item, CircleCall))
        texts = [item for item in calls if isinstance(item, TextCall)]
        diameters.append(dot.r * 2 * 512)
        symbol_sizes.append(next(item for item in texts if item.text == "▶").style.font_size * 512)
        label_sizes |= {item.style.font_size for item in texts if item.text in ("entity", "human")}
        assert diameters[-1] < side
    assert diameters == sorted(diameters) and diameters[0] < diameters[-1]
    assert symbol_sizes == sorted(symbol_sizes) and symbol_sizes[0] < symbol_sizes[-1]
    assert len(label_sizes) == 1


def test_built_in_explicit_indicator_sizes_stay_exact() -> None:
    """A size or inset given on a step is drawn as given instead of the automatic value."""
    catalog = _built_in_human_with(
        {
            "Upper clothing color": {"size": {"value": 3, "unit": "px"}},
            "Movement badge": {"font_size": {"value": 30, "unit": "px"}},
            "Confidence bar": {"inset": {"value": 7, "unit": "px"}, "top_inset": {"value": 9, "unit": "px"}},
        }
    )
    scene = _human_scene((200, 200), attributes=_RED_COLORS[:1])
    next(iter(scene.entities.values())).observations[0].classification[0].score = Score(1.0)
    calls = _record_512(scene, catalog)
    dot = next(item for item in calls if isinstance(item, CircleCall))
    assert dot.r * 2 * 512 == pytest.approx(3)
    symbol = next(item for item in calls if isinstance(item, TextCall) and item.text == "▶")
    assert symbol.style.font_size * 512 == pytest.approx(30)
    bar = next(item for item in calls if isinstance(item, BoxCall) and item.w == pytest.approx(1 / 512))
    assert (bar.x * 512, bar.y * 512) == pytest.approx((0.1 * 512 + 7, 0.2 * 512 + 9))
    assert bar.h * 512 == pytest.approx(200 - 9 - 7)


@pytest.mark.parametrize("width,height", [(10, 8), (100, 100)])
def test_built_in_confidence_bar_fills_proportionally_inside_short_boxes(width: int, height: int) -> None:
    """The confidence bar stays inside the box and its height stays proportional to the confidence."""

    def bar(confidence: float) -> BoxCall:
        scene = _human_scene((width, height))
        next(iter(scene.entities.values())).observations[0].classification[0].score = Score(confidence)
        calls = _record_512(scene)
        return next(item for item in calls if isinstance(item, BoxCall) and item.w == pytest.approx(1 / 512))

    full, quarter = bar(1.0), bar(0.25)
    assert quarter.h == pytest.approx(full.h / 4)
    assert quarter.y + quarter.h == pytest.approx(full.y + full.h)
    assert 0.1 <= full.x and 0.2 <= full.y
    assert full.y + full.h <= 0.2 + height / 512


def test_built_in_tiny_face_visibility_bar_does_not_reserve_motion_space() -> None:
    """The inner face box retains its confidence fill without reserving room for an absent badge."""
    scene = _scene(attributes=[Attribute("face_visible", 1.0)])
    observation = next(iter(scene.entities.values())).observations[0]
    observation.classification[0].type = "head"
    observation.geometry = BoundingBox.from_xywh(0.1, 0.2, 26 / 512, 29 / 512)
    boxes = [item for item in _record_512(scene) if isinstance(item, BoxCall)]
    face = min((box for box in boxes if box.style.pen_width > 0), key=lambda box: box.h)
    [bar] = [box for box in boxes if box.style.pen_width == 0 and face.x <= box.x <= face.x + face.w]
    assert bar.h > 0
    assert face.y <= bar.y < bar.y + bar.h <= face.y + face.h
    assert bar.y - face.y == pytest.approx(face.y + face.h - (bar.y + bar.h))


@pytest.mark.parametrize(
    "classification,attribute,absent,present",
    [
        ("human", "carries_bag", False, True),
        ("head", "face_visible", None, 0.0),
        ("human", "occluded", False, True),
        ("car", "occluded", False, True),
        ("head", "occluded", False, True),
        ("custom", "occluded", False, True),
    ],
)
def test_packaged_attribute_bindings_affect_output_and_ignore_wrong_shapes(
    classification: str, attribute: str, absent: object, present: object
) -> None:
    """Optional packaged analytics stay connected without fixing their visual presentation."""
    scene = _scene()
    observation = next(iter(scene.entities.values())).observations[0]
    observation.classification = [Classification(classification, Score(0.8))]
    baseline = _render_built_in(scene)
    assert baseline
    for value, changes_output in ((absent, False), (present, True), ("wrong-shape", False)):
        observation.classification[0].attributes = [Attribute(attribute, value)]
        output = _render_built_in(scene)
        assert (output != baseline) is changes_output


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


def test_rendering_entities_together_matches_rendering_each_alone() -> None:
    """Per-recipe row evaluation must give each entity exactly the output it gets on its own."""
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    for index in range(40):
        x, y = (index % 8) * 0.11, (index // 8) * 0.18
        kind = index % 6
        attributes = [
            Attribute("upper_clothing_colors", [ColorClassification("red", RGB(200, 20, 20), Score(0.8))]),
            Attribute("carries_bag", index % 2 == 0),
            Attribute("occluded", index % 3 == 0),
            Attribute("vehicle_colors", [ColorClassification("blue", RGB(0, 0, 200), Score(0.4 + index / 100))]),
            Attribute("face_visible", index / 40),
        ]
        classification = (
            [
                Classification(
                    type=("human", "car", "head", "bus", "animal")[kind], score=Score(index / 40), attributes=attributes
                )
            ]
            if kind < 5
            else []
        )
        geometry = (
            Polygon(points=[NormalizedPoint(x, y), NormalizedPoint(x + 0.08, y), NormalizedPoint(x + 0.04, y + 0.1)])
            if index % 4 == 0
            else BoundingBox.from_xywh(x, y, 0.02 + (index % 5) * 0.02, 0.05 + (index % 3) * 0.03)
        )
        velocity = ImageVelocity(vx=0.01 * (index % 4), vy=-0.005 * (index % 3)) if index % 5 else None
        entity = Entity(
            id=EntityId(f"entity-with-long-name-{index}"),
            motion_state=(None, MotionState.Moving, MotionState.Stationary, MotionState.Unknown)[index % 4],
        )
        entity.add_observation(
            Observation(
                geometry=geometry, classification=classification, frame_number=0, velocity_in_image_space=velocity
            )
        )
        scene.add_entity(entity)
    together = _render_built_in(scene)
    alone: DrawCalls = []
    for entity in scene.entities.values():
        single = Scene(time_slice=TimeSlice(start=0, end=1))
        single.add_entity(entity)
        alone.extend(_render_built_in(single))
    assert len(together) == len(alone) > 0
    assert sorted(map(repr, together)) == sorted(map(repr, alone))


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
