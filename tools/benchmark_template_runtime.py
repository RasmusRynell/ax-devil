"""Benchmark scene render template runtime paths."""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import time
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from ax_devil.modules.diagnostics.metrics_gate import set_metrics_enabled
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
from ax_devil.modules.scene.rendering.cache import CachedSceneOverlay
from ax_devil.modules.scene.rendering.catalog import (
    BUILT_IN_CATALOG_PATH,
    SceneRenderCatalogLoader,
)
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
from ax_devil.modules.settings.logging_config import get_logger, setup_logging
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext

logger = get_logger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Measure fresh preparation and cached retrieval including final backend preparation."""
    parser = argparse.ArgumentParser(description="Benchmark compiled v3 catalogs without modifying user storage.")
    parser.add_argument("--iterations", type=_positive_int, default=100)
    parser.add_argument("--entities", type=_positive_int, default=250)
    parser.add_argument("--lanes", type=_positive_int, default=1)
    parser.add_argument(
        "--hide-feature", action="append", choices=[feature.value for feature in OverlayFeature], default=[]
    )
    args = parser.parse_args(argv)
    setup_logging(console_only=True)
    set_metrics_enabled(False)
    app = QApplication.instance() or QApplication(["catalog-benchmark", "-platform", "offscreen"])
    assert app is not None
    loader = SceneRenderCatalogLoader()
    document = json.loads(BUILT_IN_CATALOG_PATH.read_text(encoding="utf-8"))
    started = time.perf_counter()
    revision = loader.validate_revision(document)
    catalog = revision.catalog
    logger.info(f"Validation and compilation: {(time.perf_counter() - started) * 1000:.2f} ms")
    if args.hide_feature:
        started = time.perf_counter()
        catalog = revision.specialize(
            OverlayVisibility(frozenset(OverlayFeature(value) for value in args.hide_feature))
        )
        logger.info(f"Visibility specialization: {(time.perf_counter() - started) * 1000:.2f} ms")
    logger.info(
        f"Python {platform.python_version()}; {platform.platform()}; "
        "diagnostics disabled; catalog and backend preparation"
    )
    logger.info(
        f"Workload: {args.entities} entities/lane; {args.lanes} lanes; {args.iterations} samples; 1280x720 target"
    )
    scenes = [_build_product_scene(args.entities) for _ in range(args.lanes)]
    contexts = [RenderContext.create(1280, 720) for _ in scenes]

    def prepare(scene: Scene, context: RenderContext) -> PreparedDrawing:
        buffer = DrawingBuffer(DrawingSettings.for_context(context))
        catalog.render_scene(scene, context, buffer)
        return buffer.finish()

    output = [prepare(scene, context) for scene, context in zip(scenes, contexts)]
    expected_counts = [len(primitives) for primitives in output]
    logger.info(f"Primitives per lane: {expected_counts}")

    def generate() -> None:
        for index, (scene, context) in enumerate(zip(scenes, contexts)):
            result = prepare(scene, context)
            assert len(result) == expected_counts[index]

    def evaluate_catalog() -> None:
        for scene, context in zip(scenes, contexts):
            buffer = DrawingBuffer(DrawingSettings.for_context(context))
            catalog.render_scene(scene, context, buffer)

    def cold_overlay() -> None:
        for scene, context in zip(scenes, contexts):
            CachedSceneOverlay(scene=scene, catalog=catalog).prepare_drawing(
                context, DrawingBuffer(DrawingSettings.for_context(context))
            )

    overlays = [CachedSceneOverlay(scene=scene, catalog=catalog) for scene in scenes]
    for overlay, context in zip(overlays, contexts):
        overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))

    def cached_overlay() -> None:
        for overlay, context in zip(overlays, contexts):
            overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))

    for label, action in (
        ("catalog evaluation", evaluate_catalog),
        ("fresh preparation", generate),
        ("cold overlay", cold_overlay),
        ("cached retrieval", cached_overlay),
    ):
        for _ in range(5):
            action()
        samples = []
        for _ in range(args.iterations):
            started = time.perf_counter()
            action()
            samples.append((time.perf_counter() - started) * 1000)
        p95 = sorted(samples)[math.ceil(len(samples) * 0.95) - 1]
        logger.info(f"{label}: median {statistics.median(samples):.3f} ms; p95 {p95:.3f} ms")
    return 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _build_product_scene(entity_count: int) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    for index in range(entity_count):
        entity = Entity(
            id=EntityId(f"entity-{index}"),
            motion_state=(MotionState.Moving, MotionState.Stationary, MotionState.Unknown)[index % 3],
        )
        entity.add_observation(_observation_for(index))
        scene.add_entity(entity)
    return scene


def _observation_for(index: int) -> Observation:
    x, y, width, height = _box_for(index)
    frame_number = index
    velocity = ImageVelocity(vx=0.002 * ((index % 9) + 1), vy=0.0015 * ((index % 7) + 1))
    classification_kind = index % 5
    if classification_kind == 0:
        return Observation(
            geometry=BoundingBox.from_xywh(x, y, width, height),
            classification=[_human_classification(index)],
            frame_number=frame_number,
            velocity_in_image_space=velocity,
        )
    if classification_kind == 1:
        return Observation(
            geometry=BoundingBox.from_xywh(x, y, width, height),
            classification=[_vehicle_classification(index)],
            frame_number=frame_number,
            velocity_in_image_space=velocity,
        )
    if classification_kind == 2:
        return Observation(
            geometry=BoundingBox.from_xywh(x, y, width, height),
            classification=[
                Classification(
                    type="head",
                    score=Score(_score_for(index)),
                    attributes=[Attribute("face_visible", 0.72)],
                )
            ],
            frame_number=frame_number,
            velocity_in_image_space=velocity,
        )
    if classification_kind == 3:
        return Observation(
            geometry=_polygon_for(x, y, width, height),
            classification=[Classification(type="license_plate", score=Score(_score_for(index)))],
            frame_number=frame_number,
            velocity_in_image_space=velocity,
        )
    return Observation(
        geometry=_polygon_for(x, y, width, height),
        frame_number=frame_number,
        velocity_in_image_space=velocity,
    )


def _human_classification(index: int) -> Classification:
    return Classification(
        type="human",
        score=Score(_score_for(index)),
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


def _vehicle_classification(index: int) -> Classification:
    vehicle_types = ("vehicle", "car", "bus", "truck", "bike", "bicycle")
    return Classification(
        type=vehicle_types[index % len(vehicle_types)],
        score=Score(_score_for(index)),
        attributes=[
            Attribute(
                "vehicle_colors",
                [
                    ColorClassification(name="blue", rgb=RGB(0, 0, 200), score=Score(0.85)),
                    ColorClassification(name="white", rgb=RGB(245, 245, 245), score=Score(0.6)),
                ],
            )
        ],
    )


def _score_for(index: int) -> float:
    return 0.35 + (index % 60) / 100


def _box_for(index: int) -> tuple[float, float, float, float]:
    column = index % 20
    row = (index // 20) % 15
    width = 0.025 + (index % 5) * 0.003
    height = 0.04 + (index % 7) * 0.003
    return 0.01 + column * 0.047, 0.02 + row * 0.06, width, height


def _polygon_for(x: float, y: float, width: float, height: float) -> Polygon:
    return Polygon(
        points=[
            NormalizedPoint(x, y),
            NormalizedPoint(x + width, y + height * 0.1),
            NormalizedPoint(x + width * 0.8, y + height),
            NormalizedPoint(x + width * 0.1, y + height * 0.8),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
