from __future__ import annotations

from ax_devil.modules.filtering import FilterState, build_default_filter_config
from ax_devil.modules.scene.filtering import filter_scene
from ax_devil.modules.scene.model import (
    KnownClassificationType,
)
from tests.helpers.entities import entity_with_classes, scene_with_entity


def test_filter_scene_applies_filter_state() -> None:
    config = build_default_filter_config()
    state = FilterState(config)
    scene = scene_with_entity(entity_with_classes(KnownClassificationType.Human.value))

    filtered = filter_scene(scene, config, state)

    assert list(filtered.entities.keys()) == ["entity-1"]

    disabled_state = FilterState(config, initial={"show_humans": False})
    disabled_scene = scene_with_entity(entity_with_classes(KnownClassificationType.Human.value))

    filtered = filter_scene(disabled_scene, config, disabled_state)

    assert filtered.entities == {}

    unmatched_scene = scene_with_entity(entity_with_classes("unexpected_type"))

    filtered = filter_scene(unmatched_scene, config, state)

    assert list(filtered.entities.keys()) == ["entity-1"]
