"""ADF motion state accepts only boolean movement values."""

from __future__ import annotations

from typing import Any

import pytest

from ax_devil.modules.scene.model import MotionState
from ax_devil.plugins.decoders.adf_v1.frame import decode_adf_frame_v1_data

_TS_ISO = "2024-07-01T12:00:00+00:00"


def _make_adf_frame(moving: object) -> dict[str, Any]:
    """Build a minimal ADF v1 frame dict with one detection; ``None`` omits the movement field."""
    detection: dict[str, Any] = {
        "object_track_id": "42",
        "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6},
        "class": {"type": "human", "score": 0.9},
    }
    if moving is not None:
        detection["moving"] = moving
    return {
        "frame": {
            "timestamp": _TS_ISO,
            "detections": [detection],
        }
    }


@pytest.mark.parametrize(
    "moving,expected",
    [(True, MotionState.Moving), (False, MotionState.Stationary), (None, None), ("false", None), (1, None)],
)
def test_adf_v1_frame_maps_only_boolean_movement(moving: object, expected: MotionState | None) -> None:
    scene = decode_adf_frame_v1_data(_make_adf_frame(moving))
    entity = next(iter(scene.entities.values()))
    assert entity.motion_state is expected
