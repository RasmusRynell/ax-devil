"""Scene inspection exposes useful entity metadata without dumping image bytes."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from ax_devil.modules.scene.inspection import (
    FieldGroup,
    entity_fields,
    entity_record,
    entity_summary,
)
from ax_devil.modules.scene.model import (
    Attribute,
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Image,
    MotionState,
    Observation,
    Score,
    WorldVelocity,
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


def test_image_fields_describe_the_data_without_dumping_it() -> None:
    entity = _make_entity()
    entity.images.append(Image(timestamp=_TS, bounding_box=BoundingBox.from_xywh(0.2, 0.3, 0.4, 0.5), data=b"abc"))

    (images,) = [items for group, items in entity_fields(entity) if group is FieldGroup.IMAGES]

    assert {item.path[-1]: item.value for item in images}["data"] == "3 bytes"
