"""Tests for the ADF Frame v1 decoder."""

from __future__ import annotations

from typing import Any

import pytest

from ax_devil.modules.scene.model import Delete, EntityId, Rename
from ax_devil.plugins.decoders.adf_v1.frame import decode_adf_frame_v1_data

_TS_ISO = "2024-07-01T12:00:00+00:00"


def _make_frame(track_events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "frame": {
            "timestamp": _TS_ISO,
            "detections": [],
            "track_events": track_events,
        }
    }


def test_adf_v1_frame_decodes_track_ended_event() -> None:
    scene = decode_adf_frame_v1_data(_make_frame([{"type": "TrackEnded", "object_track_id": "old-track"}]))

    assert len(scene.events) == 1
    event = scene.events[0]
    assert isinstance(event, Delete)
    assert event.entity_id == EntityId("old-track")


def test_adf_v1_frame_decodes_rename_event() -> None:
    scene = decode_adf_frame_v1_data(
        _make_frame(
            [
                {
                    "type": "Rename",
                    "from_id": "ed51815a-3f73-5a0c-b25e-e5b393d6bd48",
                    "to_id": "5703de7a-60ff-5b0d-88e7-5dfb2b25df15",
                }
            ]
        )
    )

    assert len(scene.events) == 1
    event = scene.events[0]
    assert isinstance(event, Rename)
    assert event.from_entity_id == EntityId("ed51815a-3f73-5a0c-b25e-e5b393d6bd48")
    assert event.to_entity_id == EntityId("5703de7a-60ff-5b0d-88e7-5dfb2b25df15")


def test_adf_v1_frame_rejects_malformed_rename_event() -> None:
    with pytest.raises(ValueError, match="Rename event missing from_id or to_id"):
        decode_adf_frame_v1_data(_make_frame([{"type": "Rename", "from_id": "old-track"}]))


def test_adf_v1_frame_rejects_unknown_track_event() -> None:
    with pytest.raises(ValueError, match="Unsupported track event type 'Merge'"):
        decode_adf_frame_v1_data(_make_frame([{"type": "Merge"}]))
