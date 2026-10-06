from __future__ import annotations

from typing import Callable

import pytest

from ax_devil.modules.filtering import FilterConfig, FilterState, build_default_filter_config
from ax_devil.modules.scene.filtering import filter_scene
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.plugins.decoders.adf_beta.common import build_adf_beta_filter_config
from ax_devil.plugins.decoders.adf_v1.common import build_adf_frame_v1_filter_config
from ax_devil.plugins.decoders.onvif_xml.decoder import build_onvif_filter_config


def _make_entity(*classification_types: str) -> Entity:
    entity = Entity(EntityId("entity-1"))
    observation = Observation(
        frame_number=0,
        geometry=BoundingBox.from_xywh(0.0, 0.0, 0.1, 0.1, allow_outside=True),
        confidence=Score(1.0),
        classification=[Classification(c_type, Score(0.9)) for c_type in classification_types],
    )
    entity.add_observation(observation)
    return entity


@pytest.mark.parametrize(
    "builder",
    [build_adf_frame_v1_filter_config, build_adf_beta_filter_config, build_onvif_filter_config],
    ids=["adf-v1", "adf-beta", "onvif"],
)
def test_decoder_filter_predicates(builder: Callable[[], FilterConfig]) -> None:
    """Decoder-specific filters include faces and distinguish unknown from known classifications."""
    config = builder()
    state = FilterState(config)
    for option_id, classification_types, expected in (
        ("show_faces", ("face",), True),
        ("show_other", ("custom_label",), True),
        ("show_other", ("bus",), False),
        ("show_other", ("human",), False),
        ("show_unknown", (), True),
    ):
        option = next(option for option in config.options if option.id == option_id)
        assert option.predicate(_make_entity(*classification_types), state) is expected


@pytest.mark.parametrize(
    "builder,extra_categories",
    [
        (build_default_filter_config, ()),
        (build_adf_frame_v1_filter_config, (("bag", "show_bags"), ("face", "show_faces"), ("custom", "show_other"))),
        (build_adf_beta_filter_config, (("bag", "show_bags"), ("face", "show_faces"), ("custom", "show_other"))),
        (build_onvif_filter_config, (("face", "show_faces"), ("custom", "show_other"))),
    ],
    ids=["default", "adf-v1", "adf-beta", "onvif"],
)
def test_supported_categories_can_be_hidden_and_reenabled(
    builder: Callable[[], FilterConfig], extra_categories: tuple[tuple[str, str], ...]
) -> None:
    """Each supported category has an effective toggle, independent of option order and labels."""
    config = builder()
    categories = (
        ("unknown", "show_unknown"),
        ("human", "show_humans"),
        ("head", "show_heads"),
        ("vehicle", "show_vehicles"),
        ("vehicle_other", "show_vehicles_other"),
        ("car", "show_cars"),
        ("bus", "show_buses"),
        ("truck", "show_trucks"),
        ("bike", "show_bikes"),
        ("bicycle", "show_bicycles"),
        ("animal", "show_animals"),
        ("license_plate", "show_license_plates"),
        *extra_categories,
    )
    for classification, option_id in categories:
        entity = _make_entity(classification)
        scene = Scene(time_slice=TimeSlice(0, 0), entities={entity.id: entity})
        state = FilterState(config)
        assert filter_scene(scene, config, state).entities == {entity.id: entity}, classification
        state.set_enabled(option_id, False)
        assert filter_scene(scene, config, state).entities == {}, classification
        state.set_enabled(option_id, True)
        assert filter_scene(scene, config, state).entities == {entity.id: entity}, classification
