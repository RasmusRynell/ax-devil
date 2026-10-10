"""One-entity scenes for filtering tests, which care about classifications and nothing else."""

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


def entity_with_classes(*classification_types: str, entity_id: str = "entity-1") -> Entity:
    """Return an entity with one observation carrying the given classification types; none means unclassified."""
    entity = Entity(EntityId(entity_id))
    entity.add_observation(
        Observation(
            frame_number=0,
            geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True),
            confidence=Score(1.0),
            classification=[Classification(c_type, Score(0.9)) for c_type in classification_types],
        )
    )
    return entity


def scene_with_entity(entity: Entity) -> Scene:
    """Return a one-entity scene."""
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    scene.add_entity(entity)
    return scene
