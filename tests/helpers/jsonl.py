"""JSON-lines files and the scene file provider built over them in data-provider tests."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy


def write_jsonl(path: Path, lines: list[dict[str, Any] | str]) -> Path:
    """Write records as JSON lines; strings are written verbatim."""
    path.write_text(
        "".join(f"{line if isinstance(line, str) else json.dumps(line)}\n" for line in lines), encoding="utf-8"
    )
    return path


def simple_test_decoder(json_string: str) -> Scene:
    """Decode one ``{"timestamp", "detections": [{"id", "class", "confidence", "bbox"}]}`` record."""
    data = json.loads(json_string)
    timestamp = datetime.fromisoformat(data.get("timestamp", "2024-01-01T12:00:00Z").replace("Z", "+00:00"))

    entities = {}
    for detection in data.get("detections", []):
        entity_id = EntityId(detection["id"])
        bbox = BoundingBox.from_xywh(*detection["bbox"])
        classification = Classification(type=detection["class"], score=Score(detection["confidence"]))
        observation = Observation(timestamp=timestamp, geometry=bbox, classification=[classification])
        entities[entity_id] = Entity(id=entity_id, observations=[observation])

    return Scene(time_slice=TimeSlice(start=timestamp, end=timestamp), entities=entities)


def build_provider(
    file_path: str | Path,
    *,
    supports_sequence_lookup: bool = False,
    storage_mode: StorageMode = StorageMode.SOURCE_INDEX,
    artifact_version: int = 1,
    decode_options: dict[str, Any] | None = None,
    timestamp_fallback_policy: TimestampFallbackPolicy = TimestampFallbackPolicy(
        tolerance_us=0,
    ),
    decoded: list[str] | None = None,
    independent_records: bool = False,
) -> SceneDecoderFileProvider:
    """Build a provider; *decoded* collects every payload the decoder parses."""

    class _TestDecoder(PayloadToSceneDecoder):
        def decode(self, payload: Any) -> Scene:
            if decoded is not None:
                decoded.append(payload)
            return simple_test_decoder(payload)

    return SceneDecoderFileProvider(
        file_path=file_path,
        decoder_factory=_TestDecoder,
        decoder_name="test_decoder",
        artifact_version=artifact_version,
        decode_options=decode_options,
        supports_sequence_lookup=supports_sequence_lookup,
        storage_mode=storage_mode,
        timestamp_fallback_policy=timestamp_fallback_policy,
        independent_records=independent_records,
    )


def jsonl_record(second: int, *entity_ids: str) -> dict[str, Any]:
    return {
        "timestamp": f"2024-01-01T12:00:{second:02d}Z",
        "detections": [
            {"id": entity_id, "class": "person", "confidence": 0.9, "bbox": [0.1, 0.2, 0.3, 0.4]}
            for entity_id in entity_ids
        ],
    }


def frame_at(timestamp_us: int, sequence_id: int = 0) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=sequence_id, timestamp_us=timestamp_us)
