"""Behavior of the beta frame format at the public decoder boundary."""

from datetime import datetime, timezone

import pytest

from ax_devil.modules.scene.model import ColorClassification, EntityId
from ax_devil.plugins.decoders.adf_beta.frame import ADFBetaFrameDecoder


def test_frame_preserves_observation_geometry_time_and_classification() -> None:
    """Frame payloads retain the information needed to place and label overlays."""
    timestamp = "2024-05-12T10:00:00Z"
    scene = ADFBetaFrameDecoder().decode(
        {
            "timestamp": timestamp,
            "observations": [
                {
                    "track_id": "track-1",
                    "timestamp": timestamp,
                    "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6},
                    "class": {"type": "Car", "score": 0.8, "colors": [{"name": "Blue", "score": 0.7}]},
                }
            ],
        }
    )

    assert scene is not None
    expected_time = datetime(2024, 5, 12, 10, tzinfo=timezone.utc)
    assert scene.time_slice.start == scene.time_slice.end == expected_time
    assert set(scene.entities) == {EntityId("track-1")}
    observation = scene.entities[EntityId("track-1")].observations[0]
    assert observation.timestamp == expected_time
    assert observation.geometry.as_xywh() == pytest.approx((0.1, 0.2, 0.3, 0.4))
    classification = observation.classification[0]
    assert classification.type == "car"
    assert classification.score.value == 0.8
    colors = classification.attribute_items("vehicle_colors", ColorClassification)
    assert [(color.name, color.score.value) for color in colors] == [("Blue", 0.7)]
