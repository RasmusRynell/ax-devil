"""Decoder and helpers for MOT Challenge CSV annotations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, List, Tuple, TypedDict

from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption
from ax_devil.modules.filtering.predicate_utils import make_classification_predicate
from ax_devil.modules.scene.model import (
    Attribute,
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_FRAME_WIDTH = 1920
DEFAULT_FRAME_HEIGHT = 1080

MOTDecoderPayload = str


class MOTBoundingBoxPayload(TypedDict):
    left: float
    top: float
    width: float
    height: float


class MOTDetectionPayload(TypedDict):
    track_id: int
    class_id: int
    confidence: float | None
    raw_score: float
    visibility: float
    source: str
    bbox: MOTBoundingBoxPayload


class MOTFramePayload(TypedDict):
    frame_index: int
    width: int
    height: int
    detections: List[MOTDetectionPayload]


@dataclass(frozen=True, slots=True)
class MOTFileStats:
    """Summary information derived while parsing a MOT annotations file."""

    total_frames: int
    total_detections: int
    max_frame_index: int


# MOT Challenge class ID mapping
MOT_CLASSES: dict[int, str] = {
    1: "person",  # Pedestrian
    2: "person_on_vehicle",  # Person on vehicle
    3: "car",  # Car
    4: "bicycle",  # Bicycle
    5: "motorcycle",  # Motorbike
    6: "vehicle",  # Non-motorized vehicle
    7: "static_person",  # Static person
    8: "distractor",  # Distractor
    9: "occluder",  # Occluder
    10: "occluder_on_ground",  # Occluder on the ground
    11: "occluder_full",  # Occluder full
    12: "reflection",  # Reflection
    13: "crowd",  # Crowd
}

__all__ = [
    "DEFAULT_FRAME_HEIGHT",
    "DEFAULT_FRAME_WIDTH",
    "MOTBoundingBoxPayload",
    "MOTDetectionPayload",
    "MOTFramePayload",
    "MOTFileStats",
    "MOT_CLASSES",
    "build_mot_filter_config",
    "decode_mot_frame",
    "prepare_mot_frame_payloads",
]


def decode_mot_frame(payload: MOTDecoderPayload) -> Scene:
    """Decode a serialised MOT frame payload into a :class:`Scene`."""
    try:
        frame_payload: MOTFramePayload = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid MOT payload: {exc}") from exc

    if not isinstance(frame_payload, dict):
        raise ValueError("Expected MOT payload to be a JSON object.")

    try:
        frame_index = int(frame_payload["frame_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("MOT payload missing 'frame_index'.") from exc

    width = int(frame_payload.get("width", DEFAULT_FRAME_WIDTH))
    height = int(frame_payload.get("height", DEFAULT_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        logger.debug(f"Invalid frame dimensions ({width}, {height}); falling back to defaults.")
        width = DEFAULT_FRAME_WIDTH
        height = DEFAULT_FRAME_HEIGHT

    detections = frame_payload.get("detections", [])
    if not isinstance(detections, list):
        raise ValueError("MOT payload 'detections' must be a list.")

    entities: dict[EntityId, Entity] = {}

    for detection_index, raw_detection in enumerate(detections):
        if not isinstance(raw_detection, dict):
            logger.debug(f"Skipping malformed detection payload: {raw_detection}")
            continue

        try:
            track_id = int(raw_detection["track_id"])
            class_id = int(raw_detection.get("class_id", -1))
            raw_score = float(raw_detection.get("raw_score", 0.0))
            confidence_payload = raw_detection.get("confidence")
            confidence = float(confidence_payload) if confidence_payload is not None else None
            visibility = float(raw_detection.get("visibility", 0.0))
            source = str(raw_detection.get("source", "unknown"))
            bbox_payload = raw_detection["bbox"]
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug(f"Skipping detection due to missing fields: {raw_detection} (error: {exc})")
            continue

        if not isinstance(bbox_payload, dict):
            logger.debug(f"Skipping detection with invalid bbox data: {bbox_payload}")
            continue

        try:
            left = float(bbox_payload["left"])
            top = float(bbox_payload["top"])
            box_width = float(bbox_payload["width"])
            box_height = float(bbox_payload["height"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug(f"Skipping detection with malformed bbox values: {bbox_payload} (error: {exc})")
            continue

        if box_width < 0 or box_height < 0:
            logger.debug(f"Skipping detection with negative dimensions: {bbox_payload}")
            continue

        normalized_bbox = BoundingBox.from_xywh(
            left / width,
            top / height,
            box_width / width,
            box_height / height,
            allow_outside=True,
        )

        label = MOT_CLASSES.get(class_id, "unknown")

        attributes = [
            Attribute("source", source),
            Attribute("raw_score", raw_score),
            Attribute("visibility_ratio", visibility),
        ]
        if source == "gt":
            attributes.append(Attribute("gt_mark", int(raw_score)))
        if confidence is not None:
            attributes.append(Attribute("confidence", confidence))

        class_score = Score(_classification_score_for_source(source, confidence))
        observation_confidence = Score(confidence) if confidence is not None else None

        observation = Observation(
            frame_number=frame_index,
            geometry=normalized_bbox,
            confidence=observation_confidence,
            classification=[Classification(label, class_score, attributes)],
        )

        entity_id = _entity_id_for_detection(track_id, frame_index, detection_index)
        entity = entities.get(entity_id)
        if entity is None:
            entity = Entity(id=entity_id, observations=[])
            entities[entity_id] = entity
        entity.add_observation(observation)

    return Scene(time_slice=TimeSlice(frame_index, frame_index), entities=entities)


def _entity_id_for_detection(track_id: int, frame_index: int, detection_index: int) -> EntityId:
    if track_id >= 0:
        return EntityId(str(track_id))
    return EntityId(f"untracked:{frame_index}:{detection_index}")


def _classification_score_for_source(source: str, confidence: float | None) -> float:
    if confidence is not None:
        return confidence
    if source == "gt":
        return 1.0
    return 0.0


def prepare_mot_frame_payloads(
    rows: Iterable[str],
    *,
    width: int = DEFAULT_FRAME_WIDTH,
    height: int = DEFAULT_FRAME_HEIGHT,
) -> Tuple[list[MOTDecoderPayload], MOTFileStats]:
    """Convert MOT CSV rows into serialised JSON payloads (one per frame)."""
    frames: dict[int, list[MOTDetectionPayload]] = {}
    total_detections = 0
    max_frame_index = -1

    for raw_line in rows:
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split(",")
        num_parts = len(parts)

        if num_parts not in (7, 9, 10):
            logger.debug(f"Skipping MOT line with unexpected column count ({num_parts}): {line}")
            continue

        try:
            frame_number = int(parts[0]) - 1
            track_id = int(parts[1])
            bb_left = float(parts[2])
            bb_top = float(parts[3])
            bb_width = float(parts[4])
            bb_height = float(parts[5])
            raw_score = float(parts[6])

            if num_parts in (7, 10):
                # DET format variants:
                # - 7 columns: frame, id, x, y, w, h, confidence
                # - 10 columns: frame, id, x, y, w, h, confidence, -1, -1, -1
                class_id = 1  # Default to pedestrian
                visibility_ratio = 1.0
                source = "det"
                confidence = raw_score if 0.0 <= raw_score <= 1.0 else None
            else:
                # GT format: frame, id, x, y, w, h, mark, class_id, visibility.
                class_id = int(parts[7])
                visibility_ratio = float(parts[8])
                source = "gt"
                confidence = None
        except (ValueError, TypeError) as exc:
            logger.debug(f"Skipping MOT line due to parse error: {line} (error: {exc})")
            continue

        if frame_number < 0:
            logger.debug(f"Skipping frame with negative index (after conversion): {frame_number}")
            continue

        max_frame_index = max(max_frame_index, frame_number)
        total_detections += 1

        bbox_payload: MOTBoundingBoxPayload = {
            "left": bb_left,
            "top": bb_top,
            "width": bb_width,
            "height": bb_height,
        }
        detection: MOTDetectionPayload = {
            "track_id": track_id,
            "class_id": class_id,
            "confidence": confidence,
            "raw_score": raw_score,
            "visibility": visibility_ratio,
            "source": source,
            "bbox": bbox_payload,
        }

        frames.setdefault(frame_number, []).append(detection)

    payloads: list[MOTDecoderPayload] = []
    for frame_number in sorted(frames.keys()):
        detections = frames[frame_number]
        payload: MOTFramePayload = {
            "frame_index": frame_number,
            "width": width,
            "height": height,
            "detections": detections,
        }
        payloads.append(json.dumps(payload, separators=(",", ":")))

    stats = MOTFileStats(
        total_frames=len(frames),
        total_detections=total_detections,
        max_frame_index=max_frame_index,
    )

    return payloads, stats


def _normalise_label(label: str) -> str:
    return label.replace("_", " ").title()


def build_mot_filter_config() -> FilterConfig:
    """Return a FilterConfig tailored to the MOT challenge label space."""
    labels = list(dict.fromkeys(MOT_CLASSES.values()))

    options: list[FilterOption] = []
    for index, label in enumerate(labels):
        options.append(
            make_classification_predicate((label,)).build_option(
                id=f"show_{label}",
                label=_normalise_label(label),
                default_enabled=True,
                sort_key=index,
            )
        )

    if "unknown" not in labels:
        options.append(
            make_classification_predicate(("unknown",)).build_option(
                id="show_unknown",
                label="Unknown",
                default_enabled=True,
                sort_key=len(options),
            )
        )

    return FilterConfig(options=tuple(options), name="mot")
