"""Tests for Entity.motion_state at model and decoder boundaries."""

from __future__ import annotations

import pickle
import re
from datetime import datetime, timezone
from typing import Any

import pytest

from ax_devil.modules.scene.inspection import build_debug_html, build_entity_hover_html
from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    BoundingBox,
    Classification,
    ColorClassification,
    Entity,
    EntityId,
    Image,
    MotionState,
    Observation,
    Score,
)
from ax_devil.plugins.decoders.adf_v1.frame import decode_adf_frame_v1_data

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_TS = datetime(2024, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
_TS_ISO = "2024-07-01T12:00:00+00:00"


def _make_adf_frame(*, moving: bool | None = None) -> dict[str, Any]:
    """Build a minimal ADF v1 frame dict with one detection."""
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


def _make_entity(*, motion_state: MotionState = MotionState.Moving) -> Entity:
    bbox = BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4)
    obs = Observation(
        geometry=bbox,
        classification=[Classification(type="human", score=Score(0.9))],
        timestamp=_TS,
    )
    entity = Entity(id=EntityId("42"), motion_state=motion_state)
    entity.add_observation(obs)
    return entity


# ---------------------------------------------------------------------------
# Entity defaults
# ---------------------------------------------------------------------------


def test_entity_motion_state_defaults_absent() -> None:
    entity = Entity(id=EntityId("1"))
    assert entity.motion_state is None
    assert "motion_state" not in build_entity_hover_html(entity)


# ---------------------------------------------------------------------------
# ADF v1 frame decoder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moving,expected",
    [(True, MotionState.Moving), (False, MotionState.Stationary), (None, None), ("false", None), (1, None)],
)
def test_adf_v1_frame_maps_only_boolean_movement(moving: object, expected: MotionState | None) -> None:
    data = _make_adf_frame()
    if moving is not None:
        data["frame"]["detections"][0]["moving"] = moving
    scene = decode_adf_frame_v1_data(data)
    entity = next(iter(scene.entities.values()))
    assert entity.motion_state is expected


# ---------------------------------------------------------------------------
# Hover card
# ---------------------------------------------------------------------------


def test_hover_html_includes_stationary_motion_state() -> None:
    entity = _make_entity(motion_state=MotionState.Stationary)
    html = build_entity_hover_html(entity)
    assert "motion_state" in html
    assert "stationary" in html


def test_hover_html_includes_unknown_motion_state() -> None:
    entity = _make_entity(motion_state=MotionState.Unknown)
    html = build_entity_hover_html(entity)
    assert "motion_state" in html
    assert "unknown" in html


def test_hover_html_includes_moving_motion_state() -> None:
    entity = _make_entity(motion_state=MotionState.Moving)
    html = build_entity_hover_html(entity)
    assert "motion_state" in html
    assert "moving" in html


def test_hover_html_includes_entity_and_observation_fields_without_field_specific_branches() -> None:
    entity = _make_entity()
    entity.end_reason = "left_scene"
    observation = entity.latest_observation
    assert observation is not None
    observation.frame_number = 17

    html = build_entity_hover_html(entity)

    assert "end_reason" in html
    assert "left_scene" in html
    assert "frame_number" in html
    assert "17" in html


def test_hover_html_surfaces_custom_classification_attributes() -> None:
    entity = _make_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.classification[0].attributes = [
        Attribute("carries_bag", False),
        Attribute("face_visible", 0.72),
        Attribute("free_text", "decoder-defined"),
        Attribute(
            "upper_body_color",
            [
                ColorClassification(name="blue", rgb=RGB(0, 0, 200), score=Score(0.85)),
                ColorClassification(name="green", rgb=RGB(0, 200, 0), score=Score(0.75)),
                ColorClassification(name="yellow", rgb=RGB(200, 200, 0), score=Score(0.65)),
                ColorClassification(name="white", rgb=RGB(245, 245, 245), score=Score(0.55)),
                ColorClassification(name="black", rgb=RGB(5, 5, 5), score=Score(0.45)),
            ],
        ),
        Attribute(
            "vehicle_type",
            [
                Classification(type="truck", score=Score(0.17)),
                Classification(type="bus", score=Score(0.08)),
            ],
        ),
    ]

    html = build_entity_hover_html(entity)

    assert "carries_bag" in html
    assert "false" in html
    assert "face_visible" in html
    assert "0.72" in html
    assert "free_text" in html
    assert "decoder-defined" in html
    assert "upper_body_color" in html
    assert "blue" in html
    assert "black" in html
    assert "vehicle_type" in html
    assert "truck" in html
    assert "bus" in html
    assert "- " in html
    assert "<br/>" in html
    assert "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;" in html


def test_hover_html_surfaces_nested_observation_debug() -> None:
    entity = _make_entity()
    observation = entity.latest_observation
    assert observation is not None
    observation.debug = {
        "motion_debug": {
            "motion_by_velocity": True,
            "velocity_estimator": {"normalized_speed": 0.42, "observation_count": 17},
        }
    }

    html = build_entity_hover_html(entity)

    assert "debug" in html
    assert "motion_debug" in html
    assert "motion_by_velocity" in html
    assert "velocity_estimator" in html
    assert "normalized_speed" in html
    assert "0.42" in html
    assert "observation_count" in html
    assert "17" in html


def test_debug_html_puts_nested_keys_on_their_own_lines() -> None:
    html = build_debug_html({"outer": {"inner": {"leaf": 0.123456}, "empty": {}}})
    lines = [re.sub(r"<[^>]+>", "", line) for line in html.split("<br/>")]

    assert lines == [
        "outer: ",
        f"{'&nbsp;' * 4}inner: ",
        f"{'&nbsp;' * 8}leaf: 0.1235",
        f"{'&nbsp;' * 4}empty: {{}}",
    ]


def test_observation_pickle_preserves_debug() -> None:
    """Native pickle restoration preserves current observation fields."""
    observation = Observation(
        geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2),
        timestamp=_TS,
        debug={"motion_debug": {"normalized_speed": 0.42}},
    )

    assert pickle.loads(pickle.dumps(observation)) == observation


def test_hover_html_surfaces_image_metadata_without_dumping_bytes() -> None:
    entity = _make_entity()
    bbox = BoundingBox.from_xywh(0.2, 0.3, 0.4, 0.5)
    entity.images.append(Image(timestamp=_TS, bounding_box=bbox, data=b"abc"))

    html = build_entity_hover_html(entity)

    assert "images" in html
    assert "timestamp" in html
    assert "bounding_box" in html
    assert "3 bytes" in html
    assert "abc" not in html
