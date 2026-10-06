"""Contracts for filtering recorded classifications without inventing Entity observations."""

from __future__ import annotations

import pytest

from ax_devil.modules.filtering import FilterConfig, FilterOption, FilterState, build_default_filter_config
from ax_devil.modules.filtering.history_filtering import history_type_filter_keeps
from ax_devil.modules.filtering.predicate_utils import (
    make_classification_predicate,
    make_other_classification_predicate,
)
from ax_devil.modules.scene.filtering import filter_scene
from ax_devil.modules.scene.model import Entity, EntityId, MotionState, Scene, TimeSlice
from ax_devil.plugins.decoders.adf_beta.common import build_adf_beta_filter_config
from ax_devil.plugins.decoders.adf_v1.common import build_adf_frame_v1_filter_config
from ax_devil.plugins.decoders.cvat.decoder import build_cvat_filter_config
from ax_devil.plugins.decoders.mot.decoder import build_mot_filter_config
from ax_devil.plugins.decoders.onvif_xml.decoder import build_onvif_filter_config


def test_default_history_filter_handles_multiple_types_unclassified_and_uncovered() -> None:
    config = build_default_filter_config()
    state = FilterState(config, initial={"show_cars": False, "show_unknown": False})

    assert not history_type_filter_keeps(("car",), config, state)
    assert not history_type_filter_keeps((), config, state)
    assert not history_type_filter_keeps(("unknown",), config, state)
    assert history_type_filter_keeps(("car", "human"), config, state)
    assert history_type_filter_keeps(("custom_decoder_type",), config, state)
    assert history_type_filter_keeps(("unknown_vehicle",), config, state)

    for option_id in config.option_ids():
        state.set_enabled(option_id, False)
    assert not history_type_filter_keeps(("custom_decoder_type",), config, state)
    assert not history_type_filter_keeps(("human",), config, state)


def test_unknown_prefix_and_other_policies_share_history_semantics() -> None:
    config = FilterConfig(
        options=(
            make_classification_predicate(("unknown",), include_empty=True).build_option(id="unknown", label="Unknown"),
            make_other_classification_predicate(("unknown", "car"), ignore_unknown_prefix=True).build_option(
                id="other", label="Other"
            ),
        )
    )
    state = FilterState(config, initial={"unknown": False})

    assert not history_type_filter_keeps(("unknown_vehicle",), config, state)
    assert not history_type_filter_keeps((), config, state)
    assert history_type_filter_keeps(("unknown_vehicle", "custom_type"), config, state)
    state.set_enabled("other", False)
    state.set_enabled("unknown", True)
    assert not history_type_filter_keeps(("custom_type",), config, state)
    assert history_type_filter_keeps((), config, state)


def test_entity_only_predicates_still_filter_scenes_but_cannot_filter_history() -> None:
    config = FilterConfig(
        options=(
            FilterOption(
                id="moving", label="Moving", predicate=lambda entity, _state: entity.motion_state is MotionState.Moving
            ),
        )
    )
    state = FilterState(config, initial={"moving": False})
    entity = Entity(id=EntityId("a"), motion_state=MotionState.Moving)
    scene = Scene(time_slice=TimeSlice(0, 0), entities={entity.id: entity})

    assert not config.supports_history_filtering
    assert filter_scene(scene, config, state).entities == {}
    state.set_enabled("moving", True)
    assert filter_scene(scene, config, state).entities == {entity.id: entity}
    with pytest.raises(ValueError, match="classification policies"):
        history_type_filter_keeps(("car",), config, state)


def test_classification_option_rejects_disagreement_between_scene_and_history_policy() -> None:
    policy = make_classification_predicate(("car",))
    with pytest.raises(ValueError, match="Entity predicate"):
        FilterOption(id="car", label="Car", predicate=lambda _entity, _state: True, classification_filter=policy)


def test_all_builtin_decoder_filters_declare_shared_classification_policies() -> None:
    configs = (
        build_default_filter_config(),
        build_onvif_filter_config(),
        build_adf_frame_v1_filter_config(),
        build_adf_beta_filter_config(),
        build_cvat_filter_config(("car", "person")),
        build_cvat_filter_config(()),
        build_mot_filter_config(),
    )
    for config in configs:
        assert config.supports_history_filtering, config.name
        assert all(option.predicate is option.classification_filter for option in config.options)

    unlabeled = build_cvat_filter_config(())
    state = FilterState(unlabeled, initial={"show_unlabeled": False})
    assert not history_type_filter_keeps((), unlabeled, state)
    state.set_enabled("show_unlabeled", True)
    assert history_type_filter_keeps((), unlabeled, state)
