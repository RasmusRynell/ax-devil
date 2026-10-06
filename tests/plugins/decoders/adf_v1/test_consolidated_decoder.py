"""Consolidated tracks preserve observations when decoded into frames."""

from datetime import datetime, timezone

import pytest

from ax_devil.modules.scene.model import EntityId
from ax_devil.plugins.decoders.adf_v1.consolidated import ADFFrameV1ConsolidatedDecoder, resolve_scenes


def test_consolidated_track_becomes_frames_with_distinct_observations() -> None:
    """Flattening a track must retain each frame's timestamp, box, and attributes."""
    scene = ADFFrameV1ConsolidatedDecoder().decode(
        {
            "id": "track-1",
            "start_time": "2024-05-12T10:00:00Z",
            "end_time": "2024-05-12T10:00:01Z",
            "duration": 1.0,
            "classes": [{"type": "Human", "score": 0.9, "carries_bag": False}],
            "path": [
                {
                    "timestamp": "2024-05-12T10:00:00Z",
                    "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6},
                },
                {
                    "timestamp": "2024-05-12T10:00:01Z",
                    "bounding_box": {"left": 0.2, "top": 0.3, "right": 0.5, "bottom": 0.7},
                },
            ],
        }
    )

    assert scene is not None
    start = datetime(2024, 5, 12, 10, tzinfo=timezone.utc)
    end = datetime(2024, 5, 12, 10, 0, 1, tzinfo=timezone.utc)
    assert (scene.time_slice.start, scene.time_slice.end) == (start, end)
    frames = resolve_scenes([scene])
    assert [frame.time_slice.start for frame in frames] == [start, end]
    for frame, timestamp, expected_box in zip(frames, (start, end), ((0.1, 0.2, 0.3, 0.4), (0.2, 0.3, 0.3, 0.4))):
        assert frame.time_slice.end == timestamp
        assert set(frame.entities) == {EntityId("track-1")}
        observations = frame.entities[EntityId("track-1")].observations
        assert len(observations) == 1
        observation = observations[0]
        assert observation.timestamp == timestamp
        assert observation.geometry.as_xywh() == pytest.approx(expected_box)
        assert observation.classification[0].type == "human"
        assert observation.classification[0].score.value == 0.9
        assert observation.classification[0].attribute_value("carries_bag") is False
