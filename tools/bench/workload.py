"""Synthetic workloads shared by the benchmarks: moving scenes, ADF files, video images and catalogs."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor, QImage

from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    BoundingBox,
    Classification,
    ColorClassification,
    Entity,
    EntityId,
    ImageVelocity,
    MotionState,
    NormalizedPoint,
    Observation,
    Polygon,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATH, SceneRenderCatalogLoader

FRAME_INTERVAL_US = 33333
_ADF_CLASSES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("human", {"upper_clothing_colors": [{"name": "white", "score": 0.8}]}),
    ("car", {"colors": [{"name": "green", "score": 0.8}]}),
    ("bicycle", {}),
    ("animal", {}),
    ("bus", {"colors": [{"name": "blue", "score": 0.8}]}),
    ("truck", {"colors": [{"name": "red", "score": 0.8}]}),
)


def load_revision(catalog: str) -> Any:
    """Load a built-in catalog by name, or a catalog file by path, and return its validated revision."""
    path = Path(catalog)
    if not path.is_file():
        path = BUILT_IN_CATALOG_PATH.with_name(f"{catalog}.json")
    return SceneRenderCatalogLoader().validate_revision(json.loads(path.read_text(encoding="utf-8")))


def moving_scenes(entity_count: int, variants: int, seed: int, *, changing_scores: bool = False) -> list[Scene]:
    """Return *variants* consecutive scenes of *entity_count* entities drifting across the frame."""
    rng = random.Random(seed)
    tracks = [
        (
            rng.uniform(0, 0.95),
            rng.uniform(0, 0.92),
            rng.uniform(0.02, 0.08),
            rng.uniform(0.04, 0.14),
            rng.uniform(-0.004, 0.004),
            rng.uniform(-0.003, 0.003),
        )
        for _ in range(entity_count)
    ]
    scenes = []
    for frame in range(variants):
        scene = Scene(time_slice=TimeSlice(start=frame, end=frame + 1))
        for index, (x, y, width, height, vx, vy) in enumerate(tracks):
            motion_state = MotionState.Moving if index % 3 != 0 else MotionState.Stationary
            entity = Entity(id=EntityId(f"lane{seed}-track-{index}"), motion_state=motion_state)
            box = ((x + vx * frame) % (1 - width), (y + vy * frame) % (1 - height), width, height)
            observation = _observation(index, frame, box, ImageVelocity(vx=vx, vy=vy))
            if changing_scores:
                for classification in observation.classification:
                    classification.score = Score(0.35 + ((index + frame * 7) % 60) / 100)
            entity.add_observation(observation)
            scene.add_entity(entity)
        scenes.append(scene)
    return scenes


def _observation(
    index: int, frame: int, box: tuple[float, float, float, float], velocity: ImageVelocity
) -> Observation:
    x, y, width, height = box
    kind = index % 5
    score = Score(0.35 + (index % 60) / 100)
    geometry: BoundingBox | Polygon = BoundingBox.from_xywh(*box)
    if kind in (3, 4):
        corners = ((0, 0), (1, 0.1), (0.8, 1), (0.1, 0.8))
        geometry = Polygon(points=[NormalizedPoint(x + width * u, y + height * v) for u, v in corners])
    vehicle = ("vehicle", "car", "bus", "truck", "bike", "bicycle")[index % 6]
    classification = {
        0: [
            Classification(
                type="human",
                score=score,
                attributes=[
                    Attribute(
                        "upper_clothing_colors",
                        [ColorClassification(name="red", rgb=RGB(200, 20, 20), score=Score(0.82))],
                    ),
                    Attribute(
                        "lower_clothing_colors",
                        [ColorClassification(name="blue", rgb=RGB(20, 40, 210), score=Score(0.76))],
                    ),
                    Attribute("carries_bag", index % 2 == 0),
                ],
            )
        ],
        1: [
            Classification(
                type=vehicle,
                score=score,
                attributes=[
                    Attribute(
                        "vehicle_colors", [ColorClassification(name="blue", rgb=RGB(0, 0, 200), score=Score(0.85))]
                    )
                ],
            )
        ],
        2: [Classification(type="head", score=score, attributes=[Attribute("face_visible", 0.7)])],
        3: [Classification(type="license_plate", score=score)],
        4: [],
    }[kind]
    return Observation(
        geometry=geometry, classification=classification, frame_number=frame, velocity_in_image_space=velocity
    )


def write_overlay_files(directory: Path, lanes: int, entities: int, variants: int) -> list[int]:
    """Write one ADF v1 JSONL file per lane with moving objects; return each frame's lookup timestamp in µs."""
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    stamps = [epoch + timedelta(microseconds=frame * FRAME_INTERVAL_US) for frame in range(variants)]
    for lane in range(lanes):
        rng = random.Random(lane)
        tracks = [
            (rng.uniform(0, 0.9), rng.uniform(0, 0.9), rng.uniform(0.02, 0.08), rng.uniform(0.04, 0.1), index)
            for index in range(entities)
        ]
        with (directory / f"lane{lane}.jsonl").open("w", encoding="utf-8") as handle:
            for frame, stamp in enumerate(stamps):
                detections = []
                for x, y, width, height, index in tracks:
                    kind, extra = _ADF_CLASSES[index % len(_ADF_CLASSES)]
                    left = (x + 0.002 * frame) % (1 - width)
                    top = (y + 0.001 * frame) % (1 - height)
                    detections.append(
                        {
                            "object_track_id": str(index),
                            "bounding_box": {"left": left, "top": top, "right": left + width, "bottom": top + height},
                            "class": {"type": kind, "score": 0.5 + (index % 50) / 100, **extra},
                            "moving": index % 3 != 0,
                            "velocity": {"pitch": 0.0, "speed": 1.5, "yaw": 2.0},
                        }
                    )
                timestamp = stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                handle.write(json.dumps({"frame": {"timestamp": timestamp, "detections": detections}}) + "\n")
    return [int(stamp.timestamp() * 1_000_000) for stamp in stamps]


def images(width: int, height: int, seed: int) -> list[QImage]:
    """Return two flat video images that differ slightly, so every frame uploads a new texture."""
    result = []
    for shade in range(2):
        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.fill(QColor(20 + seed * 10, 25 + shade * 5, 30))
        result.append(image)
    return result
