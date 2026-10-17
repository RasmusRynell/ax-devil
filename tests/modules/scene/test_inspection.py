"""Scene inspection exposes useful entity metadata without dumping image bytes."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import numpy as np
import pytest

from ax_devil.modules.scene.inspection import (
    FieldGroup,
    build_entity_hover_html,
    debug_section_html,
    entity_fields,
    entity_record,
    entity_summary,
)
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
    WorldVelocity,
)

_TS = datetime(2024, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _text(html: str) -> str:
    """Return the readable text of a hover card, one field per line."""
    return re.sub(r"<[^>]+>", "", html.replace("<br/>", "\n")).replace("&nbsp;", " ")


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


def test_hover_html_shows_motion_state_only_when_known() -> None:
    assert "motion_state" not in build_entity_hover_html(Entity(id=EntityId("1")))
    for state in MotionState:
        html = build_entity_hover_html(_make_entity(motion_state=state))
        assert "motion_state" in html
        assert state.value in html


def test_hover_html_includes_entity_and_observation_fields_without_field_specific_branches() -> None:
    entity = _make_entity()
    entity.end_reason = "left_scene"
    observation = entity.latest_observation
    assert observation is not None
    observation.frame_number = 17

    text = _text(build_entity_hover_html(entity))

    assert "end_reason: left_scene" in text
    assert "frame_number: 17" in text


@pytest.mark.parametrize("confidence", [None, Score(0.0), Score(0.8516)])
def test_hover_distinguishes_classification_score_from_optional_confidence(confidence: Score | None) -> None:
    entity = _make_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.classification = [Classification("car", Score(0.69), confidence=confidence)]

    html = build_entity_hover_html(entity)

    assert "score: 0.69" in html
    if confidence is None:
        assert "confidence" not in html
    else:
        assert f"confidence: {confidence.value:.2f}" in html


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
                Classification(type="truck", score=Score(0.17), confidence=Score(0.73)),
                Classification(type="bus", score=Score(0.08)),
            ],
        ),
    ]

    text = _text(build_entity_hover_html(entity))

    assert "carries_bag: false" in text
    assert "face_visible: 0.72" in text
    assert "free_text: decoder-defined" in text
    assert "upper_body_color:" in text
    assert "blue" in text
    assert "black" in text
    assert "vehicle_type:" in text
    assert "truck (score: 0.17, confidence: 0.73)" in text
    assert "bus" in text


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

    text = _text(build_entity_hover_html(entity))

    assert "debug" in text
    assert "motion_debug" in text
    assert re.search(r"motion_by_velocity\W*true", text)
    assert "velocity_estimator" in text
    assert re.search(r"normalized_speed\W*0\.42", text)
    assert re.search(r"observation_count\W*17", text)


def test_hover_html_surfaces_image_metadata_without_dumping_bytes() -> None:
    entity = _make_entity()
    bbox = BoundingBox.from_xywh(0.2, 0.3, 0.4, 0.5)
    entity.images.append(Image(timestamp=_TS, bounding_box=bbox, data=b"abc"))

    text = _text(build_entity_hover_html(entity))

    assert "images:" in text
    assert "timestamp=2024-07-01T12:00:00+00:00" in text
    assert "bounding_box=(0.200, 0.300, 0.400, 0.500)" in text
    assert "data=3 bytes" in text
    assert "abc" not in text
    assert "YWJj" not in text  # base64 of b"abc"


def test_hover_retains_every_debug_field() -> None:
    entity = _make_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.debug = {"recentMotion": {f"metric_{index:02d}": index for index in range(50)}}

    html = build_entity_hover_html(entity)

    assert "recentMotion" in html
    assert all(f"metric_{index:02d}" in html for index in range(50))


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
                "binary_ids": [b"\x00\xff", b"\x01\xfe"],
                "binary_markup": b"<&>",
                "empty_bytes": b"",
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
    assert r"[b&#x27;\x00\xff&#x27;, b&#x27;\x01\xfe&#x27;]" in html
    assert "b&#x27;&lt;&amp;&gt;&#x27;" in html
    assert "b&#x27;&#x27;" in html


def _inspection_entity() -> Entity:
    entity = _make_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.classification = [
        Classification("car", Score(0.69)),
        Classification("truck", Score(0.2)),
        Classification("bus", Score(7.5), attributes=[Attribute("make", "volvo")]),
    ]
    observation.velocity_in_world_space = WorldVelocity(pitch=0.0, speed=1.25, yaw=90.0)
    observation.debug = {"latency": 12, "tracker": {"state": "coasting", "hits": [3, 4]}}
    entity.end_reason = "left_scene"
    return entity


def _paths(entity: Entity, group: FieldGroup) -> dict[tuple[str, ...], object]:
    return {item.path: item.value for g, items in entity_fields(entity) if g is group for item in items}


def test_entity_fields_group_by_role_and_omit_empty_groups() -> None:
    groups = [group for group, _ in entity_fields(_inspection_entity())]
    assert groups == [
        FieldGroup.CLASSIFICATION,
        FieldGroup.MOTION,
        FieldGroup.GEOMETRY,
        FieldGroup.DEBUG,
        FieldGroup.RAW,
    ]
    assert [group for group, _ in entity_fields(Entity(id=EntityId("empty")))] == []
    assert _paths(_inspection_entity(), FieldGroup.MOTION)[("velocity_in_world_space", "speed")] == 1.25


def test_debug_values_that_cannot_be_compared_to_empty_do_not_raise() -> None:
    entity = _inspection_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.debug = {"frame": np.zeros((2, 2)), "empty": np.zeros(0)}
    assert ("frame",) in _paths(entity, FieldGroup.DEBUG)


def test_classification_candidates_get_a_fraction_only_for_scores_in_unit_range() -> None:
    items = {
        item.path: item
        for group, group_items in entity_fields(_inspection_entity())
        if group is FieldGroup.CLASSIFICATION
        for item in group_items
    }
    assert items[("type",)].value == "bus"
    assert items[("attributes", "make")].value == "volvo"
    assert items[("candidates", "car")].fraction == 0.69
    assert items[("candidates", "bus")].fraction is None
    assert [path[1] for path in items if path[0] == "candidates"] == ["bus", "car", "truck"]


def test_conf_falls_back_to_primary_score_unless_observation_reports_confidence() -> None:
    entity = _inspection_entity()
    assert entity_summary(entity).conf == 7.5
    observation = entity.latest_observation
    assert observation is not None
    observation.confidence = Score(0.97)
    assert entity_summary(entity).conf == 0.97
    assert entity_record(entity).fields["classification.conf"] == (0.97,)


def test_entity_record_exposes_debug_paths_and_never_derives_speed() -> None:
    record = entity_record(_inspection_entity())

    assert record.fields["debug.latency"] == (12,)
    assert record.fields["debug.tracker.state"] == ("coasting",)
    assert record.fields["debug.tracker.hits.1"] == (4,)
    assert record.fields["motion.state"] == ("moving",)
    assert "raw.motion_state" not in record.fields
    assert record.text == ("42", "bus", "moving")
    plain = entity_summary(_make_entity())
    assert (plain.speed, plain.class_name) == (None, "human")
    assert entity_summary(_inspection_entity()).speed == 1.25
