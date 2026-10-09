"""Scene inspection exposes useful entity metadata without dumping image bytes."""

from __future__ import annotations

from datetime import datetime, timezone

from ax_devil.modules.scene.inspection import build_entity_hover_html, debug_section_html, entity_detail_items
from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    BoundingBox,
    Classification,
    ColorClassification,
    Entity,
    EntityId,
    Image,
    MotionState,
    Observation,
    Score,
)

_TS = datetime(2024, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_entity(*, motion_state: MotionState = MotionState.Moving) -> Entity:
    bbox = BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4)
    obs = Observation(
        geometry=bbox,
        classification=[Classification(type="human", score=Score(0.9))],
        timestamp=_TS,
    )
    entity = Entity(id=EntityId("42"), motion_state=motion_state)
    entity.add_observation(obs)
    return entity


def test_entity_motion_state_defaults_absent() -> None:
    entity = Entity(id=EntityId("1"))
    assert entity.motion_state is None
    assert "motion_state" not in build_entity_hover_html(entity)


def test_hover_html_includes_stationary_motion_state() -> None:
    entity = _make_entity(motion_state=MotionState.Stationary)
    html = build_entity_hover_html(entity)
    assert "motion_state" in html
    assert "stationary" in html


def test_hover_html_includes_unknown_motion_state() -> None:
    entity = _make_entity(motion_state=MotionState.Unknown)
    html = build_entity_hover_html(entity)
    assert "motion_state" in html
    assert "unknown" in html


def test_hover_html_includes_moving_motion_state() -> None:
    entity = _make_entity(motion_state=MotionState.Moving)
    html = build_entity_hover_html(entity)
    assert "motion_state" in html
    assert "moving" in html


def test_hover_html_includes_entity_and_observation_fields_without_field_specific_branches() -> None:
    entity = _make_entity()
    entity.end_reason = "left_scene"
    observation = entity.latest_observation
    assert observation is not None
    observation.frame_number = 17

    html = build_entity_hover_html(entity)

    assert "end_reason" in html
    assert "left_scene" in html
    assert "frame_number" in html
    assert "17" in html


def test_hover_html_surfaces_custom_classification_attributes() -> None:
    entity = _make_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.classification[0].attributes = [
        Attribute("carries_bag", False),
        Attribute("face_visible", 0.72),
        Attribute("free_text", "decoder-defined"),
        Attribute(
            "upper_body_color",
            [
                ColorClassification(name="blue", rgb=RGB(0, 0, 200), score=Score(0.85)),
                ColorClassification(name="green", rgb=RGB(0, 200, 0), score=Score(0.75)),
                ColorClassification(name="yellow", rgb=RGB(200, 200, 0), score=Score(0.65)),
                ColorClassification(name="white", rgb=RGB(245, 245, 245), score=Score(0.55)),
                ColorClassification(name="black", rgb=RGB(5, 5, 5), score=Score(0.45)),
            ],
        ),
        Attribute(
            "vehicle_type",
            [
                Classification(type="truck", score=Score(0.17)),
                Classification(type="bus", score=Score(0.08)),
            ],
        ),
    ]

    html = build_entity_hover_html(entity)

    assert "carries_bag" in html
    assert "false" in html
    assert "face_visible" in html
    assert "0.72" in html
    assert "free_text" in html
    assert "decoder-defined" in html
    assert "upper_body_color" in html
    assert "blue" in html
    assert "black" in html
    assert "vehicle_type" in html
    assert "truck" in html
    assert "bus" in html
    assert "- " in html
    assert "<br/>" in html
    assert "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;" in html


def test_hover_html_surfaces_nested_observation_debug() -> None:
    entity = _make_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.debug = {
        "motion_debug": {
            "motion_by_velocity": True,
            "velocity_estimator": {"normalized_speed": 0.42, "observation_count": 17},
        }
    }

    html = build_entity_hover_html(entity)

    assert "debug" in html
    assert "motion_debug" in html
    assert "motion_by_velocity" in html
    assert "velocity_estimator" in html
    assert "normalized_speed" in html
    assert "0.42" in html
    assert "observation_count" in html
    assert "17" in html


def test_hover_html_surfaces_image_metadata_without_dumping_bytes() -> None:
    entity = _make_entity()
    bbox = BoundingBox.from_xywh(0.2, 0.3, 0.4, 0.5)
    entity.images.append(Image(timestamp=_TS, bounding_box=bbox, data=b"abc"))

    html = build_entity_hover_html(entity)

    assert "images" in html
    assert "timestamp" in html
    assert "bounding_box" in html
    assert "3 bytes" in html
    assert "abc" not in html


def test_hover_and_inspector_retain_every_debug_field() -> None:
    entity = _make_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.debug = {"recentMotion": {f"metric_{index:02d}": index for index in range(50)}}

    html = build_entity_hover_html(entity)
    details = dict(entity_detail_items(entity))["debug"]

    assert "recentMotion" in html
    assert "metric_00" in html
    assert "metric_49" in html
    assert "more" not in html
    assert all(f"metric_{index:02d}" in details for index in range(50))


def test_hover_shows_full_id_without_redundant_frame_fields() -> None:
    entity = _make_entity()
    entity.id = EntityId("12d872dd-1285-536d-bc40-123456789abc")

    html = build_entity_hover_html(entity)

    assert str(entity.id) in html
    assert "observations" not in html
    assert "timestamp" not in html
    assert _TS.isoformat() not in html


def test_debug_sections_preserve_paths_empty_values_precision_and_escaping() -> None:
    sections = debug_section_html(
        {
            "recentMotion": {
                "moving": False,
                "velocity": {"x": 0.002969, "y": 1 / 3},
                "nothing": None,
                "empty_text": "",
                "empty_map": {},
                "empty_list": [],
                "samples": [0, False, None],
                "trail": [{"<key>": "<value>"}, {"x": 0}],
            }
        }
    )
    html = "".join(sections)

    assert len(sections) == 4
    assert "recentMotion / velocity" in html
    assert "recentMotion / trail / 0" in html
    assert "recentMotion / trail / 1" in html
    assert "false" in html
    assert "null" in html
    assert "&quot;&quot;" in html
    assert "{}" in html
    assert "[]" in html
    assert "[0, false, null]" in html
    assert "0.002969" in html
    assert str(1 / 3) in html
    assert "&lt;key&gt;" in html
    assert "&lt;value&gt;" in html
