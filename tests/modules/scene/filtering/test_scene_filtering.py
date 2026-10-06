from __future__ import annotations

from ax_devil.modules.filtering import FilterState, build_default_filter_config
from ax_devil.modules.scene.filtering import filter_scene
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    KnownClassificationType,
    Observation,
    Scene,
    Score,
    TimeSlice,
)


def _build_scene(classification_type: str) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("entity-1"))
    observation = Observation(
        geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True),
        classification=[Classification(type=classification_type, score=Score(0.9))],
        frame_number=0,
    )
    entity.add_observation(observation)
    scene.add_entity(entity)
    return scene


def test_filter_scene_applies_filter_state() -> None:
    config = build_default_filter_config()
    state = FilterState(config)
    scene = _build_scene(KnownClassificationType.Human.value)

    filtered = filter_scene(scene, config, state)

    assert list(filtered.entities.keys()) == ["entity-1"]

    disabled_state = FilterState(config, initial={"show_humans": False})
    disabled_scene = _build_scene(KnownClassificationType.Human.value)

    filtered = filter_scene(disabled_scene, config, disabled_state)

    assert filtered.entities == {}

    unmatched_scene = _build_scene("unexpected_type")

    filtered = filter_scene(unmatched_scene, config, state)

    assert list(filtered.entities.keys()) == ["entity-1"]
