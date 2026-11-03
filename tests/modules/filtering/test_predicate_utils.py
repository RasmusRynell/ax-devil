"""Tests for predicate helpers used by decoder/provider filter configs."""

from __future__ import annotations

from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption, FilterState
from ax_devil.modules.filtering.predicate_utils import (
    make_classification_predicate,
    make_other_classification_predicate,
)
from tests.helpers.entities import entity_with_classes


def _dummy_state() -> FilterState:
    option = FilterOption(id="noop", label="Noop", predicate=lambda _entity, _state: True)
    config = FilterConfig(options=(option,), name="noop")
    return FilterState(config)


def test_make_classification_predicate_matches_expected() -> None:
    cases = (
        ((("car",), ("car",)), True),
        ((("car",), ("human",)), False),
        ((("car", "human"), ("human",)), True),
        ((("?3", "car"), ("?4",)), False),
        ((("?3", "car"), ("?3",)), True),
    )
    for (types, entity_classes), expected in cases:
        predicate = make_classification_predicate(types)
        entity = entity_with_classes(*entity_classes)
        assert predicate(entity, _dummy_state()) is expected

    assert make_classification_predicate(("car",), include_empty=True)(
        entity_with_classes(),
        _dummy_state(),
    )


def test_make_other_classification_predicate_matches_expected() -> None:
    predicate = make_other_classification_predicate({"car", "human"})

    entity_other = entity_with_classes("bike")
    entity_excluded = entity_with_classes("car")

    assert predicate(entity_other, _dummy_state()) is True
    assert predicate(entity_excluded, _dummy_state()) is False
