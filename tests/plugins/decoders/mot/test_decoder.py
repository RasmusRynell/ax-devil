from __future__ import annotations

import pytest

from ax_devil.modules.scene.model import EntityId, Scene
from ax_devil.plugins.decoders.mot.decoder import decode_mot_frame, prepare_mot_frame_payloads


def _decode(rows: list[str], width: int = 1920, height: int = 1080) -> list[Scene]:
    payloads, _ = prepare_mot_frame_payloads(rows, width=width, height=height)
    return [decode_mot_frame(payload) for payload in payloads]


def test_det_and_gt_rows_are_grouped_into_frames_and_unsupported_rows_skipped() -> None:
    rows = [
        "1,-1,100,100,50,50,0.9,-1,-1,-1",  # 10-column detection
        "1,1,100,100,50,50,0,1,0.8",  # 9-column ground truth
        "2,-1,100,100,50,50,0.9",  # 7-column detection
        "0,0,0,0,0,0",
        "not,a,valid,row",
        "  ",
    ]

    payloads, stats = prepare_mot_frame_payloads(rows, width=1920, height=1080)
    scenes = [decode_mot_frame(payload) for payload in payloads]

    assert (stats.total_frames, stats.total_detections) == (2, 3)
    assert [(scene.time_slice.start, len(scene.entities)) for scene in scenes] == [(0, 2), (1, 1)]


def test_gt_row_decodes_normalized_box_class_and_visibility() -> None:
    """The ground-truth mark column is a flag, never a display confidence."""
    [scene] = _decode(["1,1,0,0,64,48,1,1,0.8", "1,2,32,24,64,48,0,7,0.25"], width=640, height=480)

    observation = scene.entities[EntityId("2")].observations[0]
    assert observation.frame_number == 0
    assert observation.geometry.as_xywh() == pytest.approx((32 / 640, 24 / 480, 64 / 640, 48 / 480))
    assert observation.confidence is None
    classification = observation.classification[0]
    assert classification.type == "static_person"
    assert classification.score.value == pytest.approx(1.0)
    assert classification.attribute_value("gt_mark") == 0
    assert classification.attribute_value("visibility_ratio") == pytest.approx(0.25)


def test_untracked_det_rows_keep_every_detection() -> None:
    [scene] = _decode(
        ["1,-1,992.1,463.1,68.6,184.1,1", "1,-1,767.4,462.1,45.4,125.1,1", "1,-1,608.9,467.8,42.9,95,0.9"]
    )

    assert len(scene.entities) == 3


def test_det_scores_outside_unit_range_are_not_display_confidence() -> None:
    [scene] = _decode(["1,-1,992.1,463.1,68.6,184.1,2.3092,-1,-1,-1", "1,-1,767.4,462.1,45.4,125.1,-0.5,-1,-1,-1"])

    observations = [entity.observations[0] for entity in scene.entities.values()]
    assert [observation.confidence for observation in observations] == [None, None]
    assert [observation.classification[0].attribute_value("raw_score") for observation in observations] == [
        pytest.approx(2.3092),
        pytest.approx(-0.5),
    ]
    assert [observation.classification[0].score.value for observation in observations] == [0.0, 0.0]


def test_det_unit_scores_are_display_confidence() -> None:
    [scene] = _decode(["1,-1,992.1,463.1,68.6,184.1,0.75"])

    observation = next(iter(scene.entities.values())).observations[0]
    assert observation.confidence is not None
    assert observation.confidence.value == pytest.approx(0.75)
    assert observation.classification[0].attribute_value("raw_score") == pytest.approx(0.75)


def test_decode_mot_frame_rejects_invalid_payload() -> None:
    with pytest.raises(ValueError):
        decode_mot_frame("not-json")
