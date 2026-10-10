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
    )
    for (types, entity_classes), expected in cases:
        predicate = make_classification_predicate(types)
        entity = entity_with_classes(*entity_classes)
        assert predicate(entity, _dummy_state()) is expected

    assert make_classification_predicate(("car",), include_empty=True)(
        entity_with_classes(),
        _dummy_state(),
    )

    entity = entity_with_classes("unknown_vehicle")
    predicate_without_unknown = make_classification_predicate(("vehicle",))
    predicate_with_unknown = make_classification_predicate(("vehicle",), include_unknown_prefix=True)

    assert predicate_without_unknown(entity, _dummy_state()) is False
    assert predicate_with_unknown(entity, _dummy_state()) is True


def test_make_other_classification_predicate_matches_expected() -> None:
    predicate = make_other_classification_predicate({"car", "human"})

    entity_other = entity_with_classes("bike")
    entity_excluded = entity_with_classes("car")

    assert predicate(entity_other, _dummy_state()) is True
    assert predicate(entity_excluded, _dummy_state()) is False

    entity_unknown = entity_with_classes("unknown_vehicle")

    predicate_default = make_other_classification_predicate(())
    predicate_ignore_unknown = make_other_classification_predicate((), ignore_unknown_prefix=True)

    assert predicate_default(entity_unknown, _dummy_state()) is True
    assert predicate_ignore_unknown(entity_unknown, _dummy_state()) is False
