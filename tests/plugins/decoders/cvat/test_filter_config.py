from __future__ import annotations

from ax_devil.modules.filtering import FilterState
from ax_devil.modules.scene.model import BoundingBox, Classification, Entity, EntityId, Observation, Score
from ax_devil.plugins.decoders.cvat.decoder import build_cvat_filter_config


def _make_entity(classification_type: str | None = None) -> Entity:
    entity = Entity(EntityId("cvat-1"))
    classification = [] if classification_type is None else [Classification(classification_type, Score(1.0))]
    observation = Observation(
        frame_number=0,
        geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True),
        confidence=Score(1.0),
        classification=classification,
    )
    entity.add_observation(observation)
    return entity


def test_cvat_filter_config_builds_sorted_unique_options() -> None:
    config = build_cvat_filter_config(["car", "person", "car", "person_on_vehicle"])
    option_ids = [option.id for option in config.options]
    option_labels = [option.label for option in config.options]

    assert option_ids == ["show_car", "show_person", "show_person_on_vehicle"]
    assert option_labels == ["Car", "Person", "Person On Vehicle"]


def test_cvat_filter_predicates_match_entities() -> None:
    config = build_cvat_filter_config(["car", "person"])
    state = FilterState(config)

    car_option = next(option for option in config.options if option.id == "show_car")
    person_option = next(option for option in config.options if option.id == "show_person")

    assert car_option.predicate(_make_entity("car"), state)
    assert person_option.predicate(_make_entity("person"), state)
    assert not car_option.predicate(_make_entity("person"), state)


def test_cvat_filter_config_handles_unlabeled_data() -> None:
    config = build_cvat_filter_config([])
    state = FilterState(config)

    unlabeled_option = config.options[0]
    unlabeled_entity = _make_entity(None)

    assert unlabeled_option.id == "show_unlabeled"
    assert unlabeled_option.predicate(unlabeled_entity, state)
