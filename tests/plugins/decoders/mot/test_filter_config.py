from __future__ import annotations

from ax_devil.modules.filtering import FilterState
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Observation,
    Score,
)
from ax_devil.plugins.decoders.mot.decoder import build_mot_filter_config


def _make_entity(classification_type: str) -> Entity:
    entity = Entity(EntityId("1"))
    observation = Observation(
        frame_number=0,
        geometry=BoundingBox.from_xywh(0.0, 0.0, 0.1, 0.1, allow_outside=True),
        confidence=Score(1.0),
        classification=[Classification(classification_type, Score(1.0))],
    )
    entity.add_observation(observation)
    return entity


def test_mot_filter_config_contains_expected_options() -> None:
    config = build_mot_filter_config()
    option_ids = {option.id for option in config.options}

    assert {
        "show_person",
        "show_person_on_vehicle",
        "show_car",
        "show_bicycle",
        "show_motorcycle",
        "show_vehicle",
        "show_static_person",
        "show_distractor",
        "show_occluder",
        "show_occluder_on_ground",
        "show_occluder_full",
        "show_reflection",
        "show_crowd",
        "show_unknown",
    } == option_ids


def test_mot_filter_config_predicates_match_entities() -> None:
    config = build_mot_filter_config()
    state = FilterState(config)

    car_option = next(option for option in config.options if option.id == "show_car")
    unknown_option = next(option for option in config.options if option.id == "show_unknown")

    assert car_option.predicate(_make_entity("car"), state)
    assert unknown_option.predicate(_make_entity("unknown"), state)
    assert not car_option.predicate(_make_entity("bicycle"), state)
