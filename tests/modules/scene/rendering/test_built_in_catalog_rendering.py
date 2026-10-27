"""Drawing of the built-in catalogs for realistic Scenes."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    BoundingBox,
    Classification,
    ColorClassification,
    Entity,
    EntityId,
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
from tests.catalog_helpers import CLASSIC_CATALOG_PATH, classic_catalog, classic_variant
from tests.catalog_helpers import observed_scene as _scene
from tests.drawing_helpers import (
    BoxCall,
    CircleCall,
    DrawCalls,
    LineCall,
    TextCall,
    record_scene,
)


def _render_built_in(scene: Scene) -> DrawCalls:
    return record_scene(classic_catalog(), scene, RenderContext.create(640, 480))


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


@pytest.mark.parametrize("motion_state", [MotionState.Moving, MotionState.Stationary])
@pytest.mark.parametrize("classification", [None, "custom", "human", "car", "head"])
def test_built_in_recipes_keep_movement_and_speed_controls_independent(
    classification: str | None, motion_state: MotionState
) -> None:
    """Each packaged recipe hides its own movement badge and speed arrow, and only that, per control."""
    scene = _scene(motion_state=motion_state)
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
        assert len(boxes) >= 2  # The outer box and its score fill.


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
            "Score bar": {"inset": {"value": 7, "unit": "px"}, "top_inset": {"value": 9, "unit": "px"}},
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
def test_built_in_score_bar_fills_proportionally_inside_short_boxes(width: int, height: int) -> None:
    """The score bar stays inside the box and its height stays proportional to the score."""

    def bar(score: float) -> BoxCall:
        scene = _human_scene((width, height))
        next(iter(scene.entities.values())).observations[0].classification[0].score = Score(score)
        calls = _record_512(scene)
        return next(item for item in calls if isinstance(item, BoxCall) and item.w == pytest.approx(1 / 512))

    full, quarter = bar(1.0), bar(0.25)
    assert quarter.h == pytest.approx(full.h / 4)
    assert quarter.y + quarter.h == pytest.approx(full.y + full.h)
    assert 0.1 <= full.x and 0.2 <= full.y
    assert full.y + full.h <= 0.2 + height / 512


def test_built_in_tiny_face_visibility_bar_does_not_reserve_motion_space() -> None:
    """The inner face box retains its score fill without reserving room for an absent badge."""
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
