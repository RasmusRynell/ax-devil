from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    BoundingBox,
    Classification,
    ColorClassification,
    EntityId,
    Image,
    NormalizedPoint,
    Score,
    TimeSlice,
)
from ax_devil.plugins.decoders.adf_beta.consolidated import ADFBetaConsolidatedDecoder, resolve_scenes


def _build_track_payload(track_id: str, timestamp: str, class_type: str = "Car") -> dict[str, Any]:
    return {
        "id": track_id,
        "classes": [{"type": class_type, "score": 0.95}],
        "observations": [
            {
                "timestamp": timestamp,
                "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6},
            }
        ],
        "start_time": timestamp,
        "end_time": timestamp,
        "duration": 0.0,
    }


def test_decode_consolidated_tracks_combines_entities() -> None:
    payload = {
        "version": "1.0.0-beta1",
        "tracks": [
            _build_track_payload("track-1", "2024-05-12T10:00:00Z", "Car"),
            _build_track_payload("track-2", "2024-05-12T10:00:01Z", "Bike"),
        ],
    }

    decoder = ADFBetaConsolidatedDecoder()
    scene = decoder.decode(json.dumps(payload))

    assert scene is not None
    assert set(scene.entities) == {EntityId("track-1"), EntityId("track-2")}
    start = datetime(2024, 5, 12, 10, 0, tzinfo=timezone.utc)
    end = datetime(2024, 5, 12, 10, 0, 1, tzinfo=timezone.utc)
    assert scene.time_slice == TimeSlice(start, end)
    for entity_id, timestamp, classification in (("track-1", start, "car"), ("track-2", end, "bike")):
        entity = scene.entities[EntityId(entity_id)]
        assert len(entity.observations) == 1
        observation = entity.observations[0]
        assert observation.timestamp == timestamp
        assert observation.geometry == BoundingBox(NormalizedPoint(0.1, 0.2), NormalizedPoint(0.4, 0.6))
        assert observation.classification == [Classification(type=classification, score=Score(0.95))]


def test_track_metadata_fields_surface() -> None:
    payload = {
        "version": "1.0.0-beta1",
        "tracks": [
            {
                "id": "meta-track",
                "start_time": "2024-05-12T10:00:01Z",
                "end_time": "2024-05-12T10:00:02Z",
                "end_reason": "Ended",
                "duration": 1.0,
                "classes": [
                    {
                        "type": "Human",
                        "score": 0.88,
                        "upper_clothing_colors": [{"name": "Red", "score": 0.6}],
                        "lower_clothing_colors": [{"name": "Blue", "score": 0.4}],
                    }
                ],
                "image": {
                    "timestamp": "2024-05-12T10:00:01Z",
                    "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6},
                    "data": "ZmFrZQ==",
                },
                "observations": [
                    {
                        "timestamp": "2024-05-12T10:00:01Z",
                        "bounding_box": {"left": 0.2, "top": 0.3, "right": 0.5, "bottom": 0.5},
                    },
                    {
                        "timestamp": "2024-05-12T10:00:02Z",
                        "bounding_box": {"left": 0.3, "top": 0.4, "right": 0.6, "bottom": 0.7},
                    },
                ],
            },
        ],
    }

    decoder = ADFBetaConsolidatedDecoder()
    scene = decoder.decode(json.dumps(payload))
    assert scene is not None

    entity = scene.entities.get(EntityId("meta-track"))
    assert entity is not None
    start = datetime(2024, 5, 12, 10, 0, 1, tzinfo=timezone.utc)
    end = datetime(2024, 5, 12, 10, 0, 2, tzinfo=timezone.utc)
    assert scene.time_slice == TimeSlice(start, end)
    assert (entity.start_time, entity.end_time, entity.duration) == (start, end, 1.0)
    assert entity.images == [
        Image(
            timestamp=start,
            bounding_box=BoundingBox(NormalizedPoint(0.1, 0.2), NormalizedPoint(0.4, 0.6)),
            data=b"fake",
        )
    ]
    assert entity.end_reason == "Ended"
    assert [observation.timestamp for observation in entity.observations] == [start, end]
    assert [observation.geometry for observation in entity.observations] == [
        BoundingBox(NormalizedPoint(0.2, 0.3), NormalizedPoint(0.5, 0.5)),
        BoundingBox(NormalizedPoint(0.3, 0.4), NormalizedPoint(0.6, 0.7)),
    ]
    for observation in entity.observations:
        assert observation.classification == [
            Classification(
                type="human",
                score=Score(0.88),
                attributes=[
                    Attribute("upper_clothing_colors", [ColorClassification("Red", RGB(255, 0, 0), Score(0.6))]),
                    Attribute("lower_clothing_colors", [ColorClassification("Blue", RGB(0, 0, 255), Score(0.4))]),
                ],
            )
        ]


def test_resolve_scenes_groups_by_timestamp() -> None:
    decoder = ADFBetaConsolidatedDecoder()
    scene_a = decoder.decode(json.dumps(_build_track_payload("track-1", "2024-05-12T10:00:00Z")))
    scene_b = decoder.decode(json.dumps(_build_track_payload("track-2", "2024-05-12T10:00:00Z", "Bike")))
    scene_c = decoder.decode(json.dumps(_build_track_payload("track-1", "2024-05-12T10:00:01Z")))

    frames = list(resolve_scenes([scene_c, scene_a, None, scene_b]))
    assert [frame.time_slice for frame in frames] == [
        TimeSlice(start=timestamp, end=timestamp)
        for timestamp in (
            datetime(2024, 5, 12, 10, 0, tzinfo=timezone.utc),
            datetime(2024, 5, 12, 10, 0, 1, tzinfo=timezone.utc),
        )
    ]
    assert [set(frame.entities) for frame in frames] == [
        {EntityId("track-1"), EntityId("track-2")},
        {EntityId("track-1")},
    ]

    for frame in frames:
        for entity in frame.entities.values():
            assert len(entity.observations) == 1
            assert entity.observations[0].timestamp == frame.time_slice.start
