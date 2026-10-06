"""Measure the production viewer with several simultaneous catalog-rendered video lanes.

Each lane is a real ``VideoFrameRenderer`` in one window, fed a new moving Scene and a new
video image every frame through ``CachedSceneOverlay`` and the packaged default catalog.
Scene and image generation happen before measurement.

One sample covers one round: every lane receives a frame, renders it, and the window composites and flushes the
result. ``QQuickWidget`` does all scene-graph work on the GUI thread, so every per-frame cost is one of these stages:

- ``image``, ``catalog``, ``binding``: preparing the frame (video upload request, catalog drawing, overlay binding).
- ``quick_frame``: each lane's whole scene-graph frame — polish, sync (``updatePaintNode``), render and frame end,
  including texture uploads and command submission. ``scene_sync`` and ``render_submission`` are parts of it.
- ``compose``: the window compositing every lane's texture and flushing to the screen.
- ``gpu_wait`` (with ``--gpu-sync``): waiting for the GPU to finish the round. This serializes CPU and GPU, so it is
  an upper bound for GPU work and slows the round; leave it off when comparing CPU cost.

``frame_total`` is the sum of the GUI-thread stages; ``wall`` runs from submitting the round until it is composited;
``process_cpu`` covers every thread of the process, including graphics driver threads.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections.abc import Callable
from pathlib import Path
from statistics import median
from time import perf_counter, process_time

from benchmark_template_runtime import _human_classification, _polygon_for, _score_for, _vehicle_classification
from PySide6.QtCore import QEvent, QObject, QRectF, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QGridLayout, QWidget

from ax_devil.modules.diagnostics.metrics_gate import set_metrics_enabled
from ax_devil.modules.scene.model import (
    Attribute,
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    ImageVelocity,
    MotionState,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering.cache import CachedSceneOverlay
from ax_devil.modules.scene.rendering.catalog import (
    BUILT_IN_CATALOG_PATH,
    SceneRenderCatalog,
    SceneRenderCatalogLoader,
)
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.logging_config import get_logger, setup_logging
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer

logger = get_logger(__name__)

_STAGES = ("image", "catalog", "binding", "quick_frame", "scene_sync", "render_submission", "compose", "gpu_wait")
_FRAME_STAGES = ("image", "catalog", "binding", "quick_frame", "compose")
"""Stages that do not overlap and together are all GUI-thread work of a round."""


class EventTimer(QObject):
    """Time the whole handling of chosen events on one object by dispatching them from an event filter."""

    def __init__(
        self, target: QObject, kinds: set[QEvent.Type], active: Callable[[], bool], record: Callable[[float], None]
    ) -> None:
        super().__init__(target)
        self._target = target
        self._kinds = kinds
        self._active = active
        self._record = record
        self._inside = False
        target.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Deliver the event here and record its duration when it started during a measured round."""
        if watched is not self._target or self._inside or event.type() not in self._kinds:
            return False
        counted = self._active()
        self._inside = True
        started = perf_counter()
        try:
            watched.event(event)
        finally:
            self._inside = False
        if counted:
            self._record((perf_counter() - started) * 1000)
        return True

    def remove(self) -> None:
        """Stop timing before the target is torn down."""
        self._target.removeEventFilter(self)


class LaneViewer(VideoFrameRenderer):
    """Production viewer that attributes its GUI-thread time to the benchmark stages."""

    def __init__(self, stages: dict[str, float]) -> None:
        super().__init__()
        self._stages = stages
        self._stage_start = 0.0
        self.pending = False
        self.drawing: PreparedDrawing | None = None
        surface = self._quick
        set_frame_image, set_overlays = surface.set_frame_image, surface.set_overlays

        def timed_image(image: QImage, target: QRectF) -> None:
            started = perf_counter()
            set_frame_image(image, target)
            self._stages["image"] += (perf_counter() - started) * 1000

        def timed_overlays(
            drawing: PreparedDrawing | None,
            opacity: float,
            target: QRectF,
            scale: float,
            highlight: tuple[float, float, float, float] | None,
        ) -> None:
            started = perf_counter()
            set_overlays(drawing, opacity, target, scale, highlight)
            self._stages["binding"] += (perf_counter() - started) * 1000
            self.drawing = drawing

        surface.set_frame_image = timed_image  # type: ignore[method-assign]
        surface.set_overlays = timed_overlays  # type: ignore[method-assign]
        window = surface.quickWindow()
        window.beforeSynchronizing.connect(self._begin_stage)
        window.afterSynchronizing.connect(lambda: self._end_stage("scene_sync"))
        window.beforeRendering.connect(self._begin_stage)
        window.afterRendering.connect(lambda: self._end_stage("render_submission"))
        window.afterRendering.connect(self._rendered)
        # QQuickWidget renders a frame inside one timer event on the widget: polish, sync, render and frame end.
        self.frame_timer = EventTimer(surface, {QEvent.Type.Timer}, lambda: self.pending, self._add_quick_frame)

    def _add_quick_frame(self, elapsed: float) -> None:
        self._stages["quick_frame"] += elapsed

    def _begin_stage(self) -> None:
        self._stage_start = perf_counter()

    def _end_stage(self, name: str) -> None:
        if self.pending:
            self._stages[name] += (perf_counter() - self._stage_start) * 1000

    def _rendered(self) -> None:
        if self.pending and self.drawing is not None:
            self.pending = False


class LaneBenchmark(QWidget):
    """Submit one frame to every lane per round and record the round's cost."""

    def __init__(self, args: argparse.Namespace, catalog: SceneRenderCatalog) -> None:
        super().__init__()
        self._args = args
        self._catalog = catalog
        self._round = 0
        self._stages = dict.fromkeys(_STAGES, 0.0)
        self._samples: list[dict[str, float]] = []
        self._started = 0.0
        self._cpu_started = 0.0
        self._in_round = False
        self._uncomposed_rounds = 0
        # A round ends when the window composites it; the fallback ends a round that is never composited.
        self._fallback = QTimer(self)
        self._fallback.setSingleShot(True)
        self._fallback.setInterval(500)
        self._fallback.timeout.connect(self._uncomposed)
        self._compose_timer = EventTimer(self, {QEvent.Type.UpdateRequest}, lambda: self._in_round, self._composed)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        columns = math.ceil(math.sqrt(args.lanes))
        self._lanes = [LaneViewer(self._stages) for _ in range(args.lanes)]
        for index, lane in enumerate(self._lanes):
            layout.addWidget(lane, index // columns, index % columns)
        self._scenes = [
            _moving_scenes(args.entities, args.scene_variants, seed, changing_scores=args.changing_scores)
            for seed in range(args.lanes)
        ]
        self._images = [_images(args.video_width, args.video_height, seed) for seed in range(args.lanes)]
        self.resize(args.width, args.height)
        self.show()
        QTimer.singleShot(200, self._submit)

    def _submit(self) -> None:
        self._stages.update(dict.fromkeys(_STAGES, 0.0))
        self._started = perf_counter()
        self._cpu_started = process_time()
        self._in_round = True
        self._fallback.start()
        for index, lane in enumerate(self._lanes):
            scene = self._scenes[index][self._round % len(self._scenes[index])]
            image = self._images[index][self._round % len(self._images[index])]
            overlay = CachedSceneOverlay(scene=scene, catalog=self._catalog)
            lane.pending = True
            lane.display_frame(
                VideoFrameWithOverlays(
                    VideoFrame(image, self._round / 25, self._round),
                    VideoOverlayData(self._timed_generator(overlay), self._round / 25, self._round),
                )
            )

    def _timed_generator(
        self, overlay: CachedSceneOverlay
    ) -> Callable[[RenderContext, DrawingBuffer], PreparedDrawing]:
        def generate(context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
            started = perf_counter()
            drawing = overlay.prepare_drawing(context, buffer)
            self._stages["catalog"] += (perf_counter() - started) * 1000
            return drawing

        return generate

    def _composed(self, elapsed: float) -> None:
        self._stages["compose"] += elapsed
        if not self._in_round or any(lane.pending for lane in self._lanes):
            return
        if self._args.gpu_sync:
            rhi = self._lanes[0]._quick.quickWindow().rhi()
            if rhi is not None:
                started = perf_counter()
                rhi.finish()
                self._stages["gpu_wait"] += (perf_counter() - started) * 1000
        self._complete()

    def _uncomposed(self) -> None:
        if self._in_round and not any(lane.pending for lane in self._lanes):
            self._uncomposed_rounds += 1
            self._complete()
        elif self._in_round:
            self._fallback.start()

    def _complete(self) -> None:
        self._in_round = False
        self._fallback.stop()
        if self._round >= self._args.warmup:
            self._samples.append(
                {
                    **self._stages,
                    "frame_total": sum(self._stages[stage] for stage in _FRAME_STAGES),
                    "process_cpu": (process_time() - self._cpu_started) * 1000,
                    "wall": (perf_counter() - self._started) * 1000,
                }
            )
        self._round += 1
        if len(self._samples) >= self._args.frames:
            self._report()
            QTimer.singleShot(0, QApplication.quit)
        else:
            QTimer.singleShot(0, self._submit)

    def _report(self) -> None:
        drawing = self._lanes[0].drawing
        result = {
            "graphics_api": str(self._lanes[0]._quick.quickWindow().rendererInterface().graphicsApi()),
            "catalog": str(self._args.catalog),
            "changing_scores": self._args.changing_scores,
            "gpu_sync": self._args.gpu_sync,
            "uncomposed_rounds": self._uncomposed_rounds,
            "lanes": self._args.lanes,
            "entities_per_lane": self._args.entities,
            "video": [self._args.video_width, self._args.video_height],
            "lane_viewport": [self._lanes[0].width(), self._lanes[0].height()],
            "rounds": len(self._samples),
            "lane0_output": None
            if drawing is None
            else {
                "meshes": len(drawing.meshes),
                "paths": len(drawing.paths),
                "texts": len(drawing.texts),
                "labels": len(drawing.labels),
                "primitives": dict(drawing.counts),
            },
            "round_median_ms": {key: round(median(s[key] for s in self._samples), 2) for key in self._samples[0]},
            "round_p95_ms": {
                key: round(sorted(s[key] for s in self._samples)[int(len(self._samples) * 0.95) - 1], 2)
                for key in ("frame_total", "process_cpu", "wall")
            },
        }
        logger.warning(json.dumps(result))

    def cleanup(self) -> None:
        """Release every lane's Qt resources."""
        self._compose_timer.remove()
        for lane in self._lanes:
            lane.frame_timer.remove()
            lane.cleanup()


def _moving_scenes(entity_count: int, variants: int, seed: int, *, changing_scores: bool = False) -> list[Scene]:
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
    kind = index % 5
    geometry = _polygon_for(*box) if kind in (3, 4) else BoundingBox.from_xywh(*box)
    classification = {
        0: [_human_classification(index)],
        1: [_vehicle_classification(index)],
        2: [Classification(type="head", score=Score(_score_for(index)), attributes=[Attribute("face_visible", 0.7)])],
        3: [Classification(type="license_plate", score=Score(_score_for(index)))],
        4: [],
    }[kind]
    return Observation(
        geometry=geometry, classification=classification, frame_number=frame, velocity_in_image_space=velocity
    )


def _images(width: int, height: int, seed: int) -> list[QImage]:
    images = []
    for shade in range(2):
        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.fill(QColor(20 + seed * 10, 25 + shade * 5, 30))
        images.append(image)
    return images


def main() -> None:
    """Run one multi-lane benchmark on the desktop Qt backend."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument("--entities", type=int, default=250)
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--scene-variants", type=int, default=50)
    parser.add_argument("--video-width", type=int, default=1920)
    parser.add_argument("--video-height", type=int, default=1080)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--software", action="store_true")
    parser.add_argument("--catalog", type=Path, default=BUILT_IN_CATALOG_PATH)
    parser.add_argument("--changing-scores", action="store_true", help="Change confidence text/bars every frame.")
    parser.add_argument(
        "--gpu-sync", action="store_true", help="Wait for the GPU after each round and report it as gpu_wait."
    )
    parser.add_argument(
        "--hide-feature", action="append", choices=[feature.value for feature in OverlayFeature], default=[]
    )
    args = parser.parse_args()
    if min(args.lanes, args.entities, args.frames, args.scene_variants) < 1:
        parser.error("lanes, entities, frames and scene variants must be positive")
    setup_logging(console_only=True)
    set_metrics_enabled(False)
    preference = GraphicsAcceleration.OFF if args.software else GraphicsAcceleration.AUTO
    preference.configure()
    app = QApplication([])
    loader = SceneRenderCatalogLoader()
    document = json.loads(args.catalog.read_text(encoding="utf-8"))
    revision = loader.validate_revision(document)
    catalog = revision.specialize(OverlayVisibility(frozenset(OverlayFeature(value) for value in args.hide_feature)))
    widget = LaneBenchmark(args, catalog)
    QTimer.singleShot(120000, app.quit)
    app.exec()
    widget.cleanup()
    widget.close()
    if len(widget._samples) != args.frames:
        raise RuntimeError(f"Benchmark timed out after {len(widget._samples)} of {args.frames} measured rounds")


if __name__ == "__main__":
    main()
