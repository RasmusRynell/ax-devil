from __future__ import annotations

import pytest

from ax_devil.modules.filtering import (
    FilterConfig,
    FilterOption,
    FilterState,
    build_default_filter_config,
    iter_enabled_options,
)
from ax_devil.modules.scene.model import (
    KnownClassificationType,
)
from tests.helpers.entities import entity_with_classes


def test_filter_state_starts_from_defaults_and_rejects_unknown_options() -> None:
    config = build_default_filter_config()
    state = FilterState(config)

    # Defaults mirror the config
    assert all(state.is_enabled(option.id) == option.default_enabled for option in config.options)

    # Initial overrides succeed
    overridden_state = FilterState(config, initial={"show_humans": False})
    assert not overridden_state.is_enabled("show_humans")

    with pytest.raises(KeyError):
        FilterState(config, initial={"does_not_exist": True})

    with pytest.raises(KeyError):
        state.set_enabled("unknown_option", False)


def test_iter_enabled_options_reflects_state() -> None:
    config = build_default_filter_config()
    state = FilterState(config, initial={"show_humans": False})

    enabled_ids = [option.id for option in iter_enabled_options(config, state)]
    assert "show_humans" not in enabled_ids
    assert len(enabled_ids) == len(config.options) - 1


def test_default_predicates_cover_known_classifications() -> None:
    config = build_default_filter_config()
    state = FilterState(config)

    option = next(option for option in config.options if option.id == "show_humans")
    human_entity = entity_with_classes(KnownClassificationType.Human.value)
    assert option.predicate(human_entity, state)

    option = next(option for option in config.options if option.id == "show_bikes")
    bike_entity = entity_with_classes(KnownClassificationType.Bike.value)
    assert option.predicate(bike_entity, state)

    option = next(option for option in config.options if option.id == "show_unknown")
    unknown_entity = entity_with_classes()
    assert option.predicate(unknown_entity, state)

    unknown_type_entity = entity_with_classes("unexpected")
    assert not any(option.predicate(unknown_type_entity, state) for option in config.options)


def test_filter_config_rejects_duplicate_options() -> None:
    option = FilterOption(
        id="duplicate",
        label="Duplicate",
        predicate=lambda entity, state: True,
    )
    with pytest.raises(ValueError):
        FilterConfig(options=(option, option))
