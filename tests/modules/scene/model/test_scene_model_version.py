"""Guard that Scene model field changes come with a SCENE_MODEL_VERSION bump."""

from __future__ import annotations

import dataclasses
import inspect

from ax_devil.modules.scene import model

# Snapshot for SCENE_MODEL_VERSION (1, 0). Update together with the version.
EXPECTED_VERSION = (1, 0)
EXPECTED_FIELDS = {
    "Attribute": ("name", "value"),
    "BoundingBox": ("top_left", "bottom_right"),
    "Classification": ("type", "score", "attributes"),
    "ColorClassification": ("name", "rgb", "score"),
    "Delete": ("timestamp", "entity_id"),
    "Entity": ("id", "observations", "images", "end_reason", "motion_state"),
    "EntityRelation": ("type", "source_entity_id", "target_entity_id"),
    "Event": ("timestamp",),
    "GeographicCoordinates": ("latitude", "longitude", "elevation"),
    "Geometry": (),
    "Image": ("timestamp", "bounding_box", "data"),
    "ImageVelocity": ("vx", "vy"),
    "Merge": ("timestamp", "entity_ids", "target_entity_id"),
    "NormalizedPoint": ("x", "y", "allow_outside"),
    "Observation": (
        "geometry",
        "classification",
        "timestamp",
        "frame_number",
        "confidence",
        "velocity_in_image_space",
        "velocity_in_world_space",
        "geographical_coordinates",
        "world_coordinates",
        "debug",
    ),
    "Operation": ("timestamp",),
    "Polygon": ("points",),
    "RGB": ("r", "g", "b"),
    "Rename": ("timestamp", "from_entity_id", "to_entity_id"),
    "Scene": ("time_slice", "entities", "events", "relations", "debug"),
    "Score": ("value",),
    "SphericalCoordinates": ("distance", "azimuth", "elevation"),
    "Split": ("timestamp", "source_entity_id", "new_entity_ids"),
    "TimeSlice": ("start", "end"),
    "WorldVelocity": ("pitch", "speed", "yaw"),
}


def test_scene_model_fields_match_version_snapshot() -> None:
    """Changing Scene model fields must bump SCENE_MODEL_VERSION and this snapshot together."""
    actual = {
        name: tuple(f.name for f in dataclasses.fields(cls))
        for name, cls in vars(model).items()
        if inspect.isclass(cls) and cls.__module__ == model.__name__ and dataclasses.is_dataclass(cls)
    }
    assert (model.SCENE_MODEL_VERSION, actual) == (EXPECTED_VERSION, EXPECTED_FIELDS), (
        "Scene model changed: bump SCENE_MODEL_VERSION (major if breaking, minor if additive), "
        "then update the snapshot."
    )
