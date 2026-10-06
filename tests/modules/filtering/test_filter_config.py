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
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    KnownClassificationType,
    Observation,
    Score,
)


def _make_entity(*, classification_types: tuple[str, ...] = (), include_empty: bool = False) -> Entity:
    geometry = BoundingBox.from_xywh(0.0, 0.0, 0.1, 0.1)
    if include_empty:
        classifications = []
    else:
        classifications = [
            Classification(type=classification_type, score=Score(1.0)) for classification_type in classification_types
        ]
    observation = Observation(geometry=geometry, classification=classifications, frame_number=0)
    return Entity(id=EntityId("entity"), observations=[observation])


def test_filter_state_initialisation_and_mutation() -> None:
    config = build_default_filter_config()
    state = FilterState(config)

    # Defaults mirror the config
    assert all(state.is_enabled(option.id) == option.default_enabled for option in config.options)

    # Initial overrides succeed
    overridden_state = FilterState(config, initial={"show_humans": False})
    assert not overridden_state.is_enabled("show_humans")
    overridden_state.set_enabled("show_humans", True)
    assert overridden_state.is_enabled("show_humans")

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
    human_entity = _make_entity(classification_types=(KnownClassificationType.Human.value,))
    assert option.predicate(human_entity, state)

    option = next(option for option in config.options if option.id == "show_bikes")
    bike_entity = _make_entity(classification_types=(KnownClassificationType.Bike.value,))
    assert option.predicate(bike_entity, state)

    option = next(option for option in config.options if option.id == "show_unknown")
    unknown_entity = _make_entity(include_empty=True)
    assert option.predicate(unknown_entity, state)

    unknown_type_entity = _make_entity(classification_types=("unexpected",))
    assert not any(option.predicate(unknown_type_entity, state) for option in config.options)


def test_filter_config_rejects_duplicate_options() -> None:
    option = FilterOption(
        id="duplicate",
        label="Duplicate",
        predicate=lambda entity, state: True,
    )
    with pytest.raises(ValueError):
        FilterConfig(options=(option, option))
