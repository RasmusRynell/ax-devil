from __future__ import annotations

import json

import pytest

from ax_devil.modules.scene.model import BoundingBox, EntityId
from ax_devil.plugins.decoders.mot.decoder import decode_mot_frame, prepare_mot_frame_payloads


def test_prepare_payloads_groups_rows_by_frame() -> None:
    rows = [
        "1,10,10,20,30,40,0.9,1,0.75",
        "1,11,30,40,50,60,0.5,3,0.12",
        "2,15,5,10,10,15,0.8,99,0.2",
        "  ",
        "not,a,valid,row",
    ]

    payloads, stats = prepare_mot_frame_payloads(rows, width=640, height=480)

    assert len(payloads) == 2
    assert stats.total_detections == 3
    assert stats.max_frame_index == 1

    first_payload = json.loads(payloads[0])
    assert first_payload["frame_index"] == 0
    assert first_payload["width"] == 640
    assert first_payload["height"] == 480
    assert len(first_payload["detections"]) == 2


def test_decode_mot_frame_builds_scene_with_entities() -> None:
    rows = [
        "1,1,0,0,64,48,0.9,1,0.8",
        "1,2,32,24,64,48,0.7,7,0.25",
    ]
    payloads, _ = prepare_mot_frame_payloads(rows, width=640, height=480)
    scene = decode_mot_frame(payloads[0])

    assert scene.time_slice.start == 0
    assert scene.time_slice.end == 0
    assert len(scene.entities) == 2

    entity = scene.entities[EntityId("2")]
    observation = entity.observations[0]
    assert isinstance(observation.geometry, BoundingBox)
    assert observation.frame_number == 0

    assert observation.geometry.as_xywh() == pytest.approx((32 / 640, 24 / 480, 64 / 640, 48 / 480))

    classification = observation.classification[0]
    assert classification.type == "static_person"
    attribute_names = {attribute.name for attribute in classification.attributes}
    assert {"visibility_ratio", "gt_mark"} <= attribute_names


def test_prepare_payloads_accepts_7_column_det_rows() -> None:
    rows = [
        "1,-1,992.1,463.1,68.6,184.1,1",
        "1,-1,767.4,462.1,45.4,125.1,1",
        "2,-1,608.9,467.8,42.9,95,0.9",
    ]

    payloads, stats = prepare_mot_frame_payloads(rows, width=1920, height=1080)

    assert len(payloads) == 2
    assert stats.total_frames == 2
    assert stats.total_detections == 3
    assert stats.max_frame_index == 1

    first_payload = json.loads(payloads[0])
    assert first_payload["frame_index"] == 0
    assert len(first_payload["detections"]) == 2

    first_detection = first_payload["detections"][0]
    assert first_detection["class_id"] == 1
    assert first_detection["visibility"] == pytest.approx(1.0)


def test_decode_det_rows_with_untracked_ids_keeps_all_detections() -> None:
    rows = [
        "1,-1,992.1,463.1,68.6,184.1,1",
        "1,-1,767.4,462.1,45.4,125.1,1",
        "1,-1,608.9,467.8,42.9,95,0.9",
    ]
    payloads, _ = prepare_mot_frame_payloads(rows, width=1920, height=1080)

    scene = decode_mot_frame(payloads[0])

    assert len(scene.entities) == 3
    assert set(scene.entities) == {"untracked:0:0", "untracked:0:1", "untracked:0:2"}


def test_decode_det_raw_scores_outside_unit_range_are_not_display_confidence() -> None:
    rows = [
        "1,-1,992.1,463.1,68.6,184.1,2.3092,-1,-1,-1",
        "1,-1,767.4,462.1,45.4,125.1,-0.5,-1,-1,-1",
    ]
    payloads, _ = prepare_mot_frame_payloads(rows, width=1920, height=1080)

    scene = decode_mot_frame(payloads[0])

    observations = [entity.latest_observation for entity in scene.entities.values()]
    assert all(observation is not None for observation in observations)
    assert all(observation.confidence is None for observation in observations if observation is not None)

    raw_scores = [
        observation.classification[0].attribute_value("raw_score")
        for observation in observations
        if observation is not None
    ]
    assert raw_scores == [pytest.approx(2.3092), pytest.approx(-0.5)]
    assert all(
        observation.classification[0].score.value == pytest.approx(0.0)
        for observation in observations
        if observation is not None
    )


def test_decode_det_unit_scores_are_display_confidence() -> None:
    rows = ["1,-1,992.1,463.1,68.6,184.1,0.75"]
    payloads, _ = prepare_mot_frame_payloads(rows, width=1920, height=1080)

    scene = decode_mot_frame(payloads[0])
    observation = next(iter(scene.entities.values())).latest_observation

    assert observation is not None
    assert observation.confidence is not None
    assert observation.confidence.value == pytest.approx(0.75)
    assert observation.classification[0].attribute_value("confidence") == pytest.approx(0.75)
    assert observation.classification[0].attribute_value("raw_score") == pytest.approx(0.75)


def test_decode_gt_mark_is_not_display_confidence() -> None:
    rows = ["1,1,912,484,97,109,0,7,0.5"]
    payloads, _ = prepare_mot_frame_payloads(rows, width=1920, height=1080)

    scene = decode_mot_frame(payloads[0])
    observation = next(iter(scene.entities.values())).latest_observation

    assert observation is not None
    assert observation.confidence is None
    classification = observation.classification[0]
    assert classification.type == "static_person"
    assert classification.score.value == pytest.approx(1.0)
    assert classification.attribute_value("gt_mark") == 0
    assert classification.attribute_value("visibility_ratio") == pytest.approx(0.5)


def test_decode_mot_frame_handles_invalid_payload() -> None:
    with pytest.raises(ValueError):
        decode_mot_frame("not-json")


class TestMOTDecoder10Column:
    """Verify the MOT decoder handles both 9-column and 10-column formats."""

    @pytest.mark.parametrize(
        ("rows", "expected_frames", "expected_detections"),
        [
            (
                [
                    "1,-1,772.68,455.43,41.871,127.61,2.1262,-1,-1,-1",
                    "1,-1,717.79,451.29,44.948,136.84,1.7969,-1,-1,-1",
                    "2,-1,772.68,455.43,41.871,127.61,2.1551,-1,-1,-1",
                ],
                2,
                3,
            ),
            (
                [
                    "1,1,912,484,97,109,0,7,0.2",
                    "1,2,1338,418,121,166,0,7,0.4",
                ],
                1,
                2,
            ),
        ],
    )
    def test_supported_det_and_gt_formats(
        self,
        rows: list[str],
        expected_frames: int,
        expected_detections: int,
    ) -> None:
        from ax_devil.plugins.decoders.mot.decoder import prepare_mot_frame_payloads

        payloads, stats = prepare_mot_frame_payloads(rows, width=1920, height=1080)
        assert payloads
        assert stats.total_frames == expected_frames
        assert stats.total_detections == expected_detections

    def test_mixed_formats_skips_unknown(self) -> None:
        from ax_devil.plugins.decoders.mot.decoder import prepare_mot_frame_payloads

        rows = [
            "1,-1,100,100,50,50,0.9,-1,-1,-1",  # 10-col
            "1,1,100,100,50,50,0.9,1,0.8",  # 9-col
            "1,1,100,100,50,50,0.9",  # 7-col
            "0,0,0,0,0,0",  # 6-col → skipped
            "not,a,valid,row",  # invalid → skipped
        ]
        payloads, stats = prepare_mot_frame_payloads(rows, width=1920, height=1080)
        assert stats.total_detections == 3
