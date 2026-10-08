"""Tests for predicate helpers used by decoder/provider filter configs."""

from __future__ import annotations

from collections.abc import Sequence

from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption, FilterState
from ax_devil.modules.filtering.predicate_utils import (
    make_classification_predicate,
    make_other_classification_predicate,
)
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Observation,
    Score,
)


def _dummy_state() -> FilterState:
    option = FilterOption(id="noop", label="Noop", predicate=lambda _entity, _state: True)
    config = FilterConfig(options=(option,), name="noop")
    return FilterState(config)


def _entity_with_classes(class_types: Sequence[str]) -> Entity:
    entity = Entity(id=EntityId("entity"))
    classifications = [Classification(type=cls, score=Score(0.9)) for cls in class_types]
    observation = Observation(
        geometry=BoundingBox.from_xywh(0.0, 0.0, 1.0, 1.0),
        classification=classifications,
        frame_number=0,
    )
    entity.add_observation(observation)
    return entity


def _entity_without_classifications() -> Entity:
    entity = Entity(id=EntityId("empty"))
    observation = Observation(
        geometry=BoundingBox.from_xywh(0.0, 0.0, 1.0, 1.0),
        classification=[],
        frame_number=0,
    )
    entity.add_observation(observation)
    return entity


def test_make_classification_predicate_matches_expected() -> None:
    cases = (
        ((("car",), ("car",)), True),
        ((("car",), ("human",)), False),
        ((("car", "human"), ("human",)), True),
    )
    for (types, entity_classes), expected in cases:
        predicate = make_classification_predicate(types)
        entity = _entity_with_classes(entity_classes)
        assert predicate(entity, _dummy_state()) is expected

    assert make_classification_predicate(("car",), include_empty=True)(
        _entity_without_classifications(),
        _dummy_state(),
    )

    entity = _entity_with_classes(("unknown_vehicle",))
    predicate_without_unknown = make_classification_predicate(("vehicle",))
    predicate_with_unknown = make_classification_predicate(("vehicle",), include_unknown_prefix=True)

    assert predicate_without_unknown(entity, _dummy_state()) is False
    assert predicate_with_unknown(entity, _dummy_state()) is True


def test_make_other_classification_predicate_matches_expected() -> None:
    predicate = make_other_classification_predicate({"car", "human"})

    entity_other = _entity_with_classes(("bike",))
    entity_excluded = _entity_with_classes(("car",))

    assert predicate(entity_other, _dummy_state()) is True
    assert predicate(entity_excluded, _dummy_state()) is False

    entity_unknown = _entity_with_classes(("unknown_vehicle",))

    predicate_default = make_other_classification_predicate(())
    predicate_ignore_unknown = make_other_classification_predicate((), ignore_unknown_prefix=True)

    assert predicate_default(entity_unknown, _dummy_state()) is True
    assert predicate_ignore_unknown(entity_unknown, _dummy_state()) is False
