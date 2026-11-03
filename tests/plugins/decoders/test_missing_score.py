"""A source that carries no score keeps its objects: decoders never drop a sample or invent a score."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ax_devil.modules.scene.model import Scene
from ax_devil.plugins.decoders.adf_beta.consolidated import ADFBetaConsolidatedDecoder
from ax_devil.plugins.decoders.adf_beta.frame import ADFBetaFrameDecoder
from ax_devil.plugins.decoders.adf_v1.consolidated import ADFFrameV1ConsolidatedDecoder
from ax_devil.plugins.decoders.adf_v1.frame import decode_adf_frame_v1_data
from ax_devil.plugins.decoders.onvif_xml.decoder import decode_onvif_xml

_TS = "2024-05-12T10:00:00Z"
_BOX = {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6}


def _v1_frame() -> Scene | None:
    detection = {"object_track_id": "t", "bounding_box": _BOX, "class": {"type": "human"}}
    return decode_adf_frame_v1_data({"frame": {"timestamp": _TS, "detections": [detection]}})


def _v1_consolidated() -> Scene | None:
    track = {
        "id": "t",
        "start_time": _TS,
        "end_time": _TS,
        "duration": 0.0,
        "classes": [{"type": "Human"}],
        "path": [{"timestamp": _TS, "bounding_box": _BOX}],
    }
    return ADFFrameV1ConsolidatedDecoder().decode(track)


def _beta_frame() -> Scene | None:
    observation = {"track_id": "t", "timestamp": _TS, "bounding_box": _BOX, "class": {"type": "LicensePlate"}}
    return ADFBetaFrameDecoder().decode({"timestamp": _TS, "observations": [observation]})


def _beta_consolidated() -> Scene | None:
    track: dict[str, Any] = {
        "id": "t",
        "classes": [{"type": "Human"}],
        "observations": [{"timestamp": _TS, "bounding_box": _BOX}],
        "start_time": _TS,
        "end_time": _TS,
        "duration": 0.0,
    }
    return ADFBetaConsolidatedDecoder().decode(json.dumps({"version": "1.0.0-beta1", "tracks": [track]}))


def _onvif() -> Scene | None:
    xml = (Path(__file__).parent / "onvif_xml" / "data" / "sample_frame.xml").read_text()
    xml = xml.replace('=" ', '="').replace(' " ', '" ').replace("SampleFrame", "Frame")
    return decode_onvif_xml(re.sub(r'(<tt:Type) Likelihood="[^"]*"', r"\1", xml))


@pytest.mark.parametrize("decode", [_v1_frame, _v1_consolidated, _beta_frame, _beta_consolidated, _onvif])
def test_a_missing_score_keeps_the_sample(decode: Callable[[], Scene | None]) -> None:
    scene = decode()

    assert scene is not None and scene.entities
    for entity in scene.entities.values():
        classification = entity.observations[0].classification[0]
        assert (classification.score, classification.confidence) == (None, None)
