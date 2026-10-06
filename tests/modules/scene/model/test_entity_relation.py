from __future__ import annotations

import pickle

import pytest

from ax_devil.modules.scene.model import EntityId, EntityRelation, Scene, TimeSlice


def test_entity_relation_requires_a_type() -> None:
    with pytest.raises(ValueError, match="type cannot be empty"):
        EntityRelation(
            type="",
            source_entity_id=EntityId("source"),
            target_entity_id=EntityId("target"),
        )


def test_scene_deduplicates_identical_relations() -> None:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    relation = EntityRelation(
        type="has_part",
        source_entity_id=EntityId("source"),
        target_entity_id=EntityId("target"),
    )

    scene.add_relation(relation)
    scene.add_relation(relation)

    assert scene.relations == {relation}


def test_scene_pickle_preserves_relations() -> None:
    """Native pickle restoration preserves current scene relations."""
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    scene.add_relation(
        EntityRelation(
            type="has_part",
            source_entity_id=EntityId("source"),
            target_entity_id=EntityId("target"),
        )
    )

    restored_scene = pickle.loads(pickle.dumps(scene))

    assert restored_scene == scene
