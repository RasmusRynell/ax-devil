"""Decode UVG-VCM's normalized annotations without fabricating timing or confidence."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator
from math import isfinite
from pathlib import Path
from typing import Any

from ax_devil.modules.filtering import FilterConfig
from ax_devil.modules.filtering.predicate_utils import make_classification_predicate
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import (
    Attribute,
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Geometry,
    NormalizedPoint,
    Observation,
    Polygon,
    Scene,
    Score,
    TimeSlice,
)

# UVG-VCM uses contiguous IDs 1..80, not the sparse category IDs in COCO JSON exports.
# Source: https://tie-ultravideo.rd.tuni.fi/UVG-VCM/index.html
CLASS_NAMES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context}: expected a JSON object")
    return value


def _array(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context}: expected a JSON array")
    return value


def _integer(value: object, context: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{context}: expected an integer >= {minimum}")
    return value


def _number(value: object, context: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
        raise ValueError(f"{context}: expected a finite number")
    return float(value)


def iter_uvg_vcm_frames(file_path: Path) -> Iterator[str]:
    """Validate a v1.0 document and yield each source frame as a decoder payload."""
    document = _object(json.loads(file_path.read_text(encoding="utf-8")), "UVG-VCM document")
    if document.pop("version", None) != "1.0":
        raise ValueError("UVG-VCM annotations must declare version '1.0'")
    if not document or set(document) != {f"{index}" for index in range(1, len(document) + 1)}:
        raise ValueError("UVG-VCM frame keys must be consecutive integers starting at '1', including empty frames")
    for index in range(len(document)):
        yield json.dumps({"frame_index": index, "detections": document[f"{index + 1}"]})


def _geometry(detection: dict[str, Any], context: str) -> Geometry:
    x_min, y_min, x_max, y_max = (
        _number(detection.get(name), f"{context}.{name}") for name in ("x_min", "y_min", "x_max", "y_max")
    )
    if x_max < x_min or y_max < y_min:
        raise ValueError(f"{context}: bounding-box maxima must not be below minima")
    if "polygon" in detection:
        coordinates = _array(detection["polygon"], f"{context}.polygon")
        if len(coordinates) < 6 or len(coordinates) % 2:
            raise ValueError(f"{context}.polygon: expected at least three x/y coordinate pairs")
        values = [_number(value, f"{context}.polygon") for value in coordinates]
        return Polygon([NormalizedPoint(x, y, allow_outside=True) for x, y in zip(values[::2], values[1::2])])
    # Reviewed source annotations sometimes extend slightly beyond 0..1; do not clamp the authors' geometry.
    return BoundingBox.from_xywh(x_min, y_min, x_max - x_min, y_max - y_min, allow_outside=True)


class UVGVCMFrameDecoder(PayloadToSceneDecoder):
    """Decode one internal frame payload; this is not a streaming annotation format."""

    def decode(self, payload: Any) -> Scene:
        """Preserve every detection and separate ambiguous IDs instead of silently merging objects."""
        frame = _object(json.loads(payload), "UVG-VCM frame")
        frame_index = _integer(frame.get("frame_index"), "frame_index")
        raw_detections = _array(frame.get("detections"), f"Frame {frame_index + 1}")
        detections = [
            _object(value, f"Frame {frame_index + 1} detection {i}") for i, value in enumerate(raw_detections)
        ]
        track_ids = [_integer(d.get("track_id"), f"Frame {frame_index + 1}.track_id") for d in detections]
        counts = Counter(track_ids)
        duplicates = sorted(track_id for track_id, count in counts.items() if count > 1)
        scene = Scene(
            time_slice=TimeSlice(frame_index, frame_index),
            debug={"duplicate_track_ids": duplicates},
        )
        for index, (detection, track_id) in enumerate(zip(detections, track_ids)):
            context = f"Frame {frame_index + 1} detection {index}"
            class_id = _integer(detection.get("class_id"), f"{context}.class_id", minimum=1)
            if class_id > len(CLASS_NAMES):
                raise ValueError(f"{context}.class_id: expected a UVG-VCM category ID in 1..80")
            class_name = CLASS_NAMES[class_id - 1]
            if "class_name" in detection and detection["class_name"] != class_name:
                raise ValueError(f"{context}.class_name: does not match category {class_id} ({class_name})")
            entity_id = (
                EntityId(f"ambiguous:{frame_index}:{track_id}:{index}")
                if counts[track_id] > 1
                else EntityId(f"{track_id}")
            )
            attributes = [Attribute("class_id", class_id), Attribute("track_id", track_id)]
            attributes.extend(
                Attribute(name, detection[name]) for name in ("iscrowd", "mask_color") if name in detection
            )
            observation = Observation(
                frame_number=frame_index,
                geometry=_geometry(detection, context),
                classification=[Classification(class_name, Score(1.0), attributes)],
                debug={"uvg_vcm": detection},
            )
            scene.add_entity(Entity(entity_id, observations=[observation]))
        return scene


def build_uvg_vcm_filter_config(labels: Iterable[str]) -> FilterConfig:
    """Expose the actual sequence's classes to Scene and whole-file history filtering."""
    options = tuple(
        make_classification_predicate((label,)).build_option(
            id=f"show_{label.replace(' ', '_')}", label=label.title(), sort_key=index
        )
        for index, label in enumerate(sorted(set(labels)))
    )
    return FilterConfig(
        name="uvg_vcm",
        options=options
        or (
            make_classification_predicate((), include_empty=True).build_option(
                id="show_unclassified", label="Unclassified"
            ),
        ),
    )
