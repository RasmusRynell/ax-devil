from __future__ import annotations

from ax_devil.modules.filtering import FilterState
from ax_devil.plugins.decoders.cvat.decoder import build_cvat_filter_config
from tests.helpers.entities import entity_with_classes


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

    assert car_option.predicate(entity_with_classes("car"), state)
    assert person_option.predicate(entity_with_classes("person"), state)
    assert not car_option.predicate(entity_with_classes("person"), state)


def test_cvat_filter_config_handles_unlabeled_data() -> None:
    config = build_cvat_filter_config([])
    state = FilterState(config)

    unlabeled_option = config.options[0]
    unlabeled_entity = entity_with_classes()

    assert unlabeled_option.id == "show_unlabeled"
    assert unlabeled_option.predicate(unlabeled_entity, state)
