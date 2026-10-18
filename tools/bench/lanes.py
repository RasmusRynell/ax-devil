"""Several production viewers in one window, each fed a new moving Scene and video image per round.

One sample is one round: every lane gets a frame, renders it, and the window composites the result. Stages, all
GUI-thread time: ``decode`` (``--decode``: a synthetic ADF v1 file via ``FileOverlaySource``), ``image``, ``filter``,
``catalog`` (the ``stages.py`` stages are parts of it), ``binding``, ``quick_frame`` (the whole scene-graph frame;
``scene_sync`` and ``render_submission`` are parts of it), ``compose`` and, with ``--gpu-sync``, ``gpu_wait``.
``frame_total`` sums the non-overlapping ones, ``wall`` is submit to composition, ``rest`` the difference, and
``process_cpu_ms`` includes driver threads. Rounds run back to back unless ``--fps`` paces them, so unpaced ``lane_fps``
is the most the machine sustains. ``--interaction pan|zoom`` keeps one frame and moves the viewport every round.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter, process_time
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QEvent, QObject, QRectF, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QGridLayout, QWidget

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.diagnostics.metrics_gate import set_metrics_enabled
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.rendering.cache import CachedSceneOverlay
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
from ax_devil.modules.settings.logging_config import setup_logging
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer
from ax_devil.modules.video_player.engine.viewport_state import NormalizedViewport
from ax_devil.plugins.decoders.adf_v1.frame import ADFFrameV1DataProvider

from . import report, stages, workload

_LANE_STAGES = (
    "decode",
    "image",
    "filter",
    "catalog",
    "binding",
    "quick_frame",
    "scene_sync",
    "render_submission",
    "compose",
    "gpu_wait",
)
_FRAME_STAGES = ("decode", "image", "filter", "catalog", "binding", "quick_frame", "compose")
"""Stages that do not overlap and together are all GUI-thread work of a round."""
_VIDEO = (1920, 1080)
_VARIANTS = 50


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

    def __init__(self, totals: dict[str, float]) -> None:
        super().__init__()
        self._totals = totals
        self._stage_start = 0.0
        self.pending = False
        self.drawing: PreparedDrawing | None = None
        surface = self._quick
        set_frame_image, set_overlays = surface.set_frame_image, surface.set_overlays

        def timed_image(image: QImage, target: QRectF) -> None:
            started = perf_counter()
            set_frame_image(image, target)
            self._totals["image"] += (perf_counter() - started) * 1000

        def timed_overlays(drawing: PreparedDrawing | None, *rest: Any) -> None:
            started = perf_counter()
            set_overlays(drawing, *rest)
            self._totals["binding"] += (perf_counter() - started) * 1000
            self.drawing = drawing

        setattr(surface, "set_frame_image", timed_image)
        setattr(surface, "set_overlays", timed_overlays)
        window = surface.quickWindow()
        window.beforeSynchronizing.connect(self._begin_stage)
        window.afterSynchronizing.connect(lambda: self._end_stage("scene_sync"))
        window.beforeRendering.connect(self._begin_stage)
        window.afterRendering.connect(lambda: self._end_stage("render_submission"))
        window.afterRendering.connect(self._rendered)
        # QQuickWidget renders a frame inside one timer event on the widget: polish, sync, render and frame end.
        self.frame_timer = EventTimer(surface, {QEvent.Type.Timer}, lambda: self.pending, self._add_quick_frame)

    def _add_quick_frame(self, elapsed: float) -> None:
        self._totals["quick_frame"] += elapsed

    def _begin_stage(self) -> None:
        self._stage_start = perf_counter()

    def _end_stage(self, name: str) -> None:
        if self.pending:
            self._totals[name] += (perf_counter() - self._stage_start) * 1000

    def _rendered(self) -> None:
        if self.pending and self.drawing is not None:
            self.pending = False


class LaneBenchmark(QWidget):
    """Submit one frame to every lane per round and record the round's cost."""

    def __init__(self, args: Any, catalog: Any, totals: dict[str, float]) -> None:
        super().__init__()
        self._args = args
        self._catalog = catalog
        self._totals = totals
        self._round = 0
        self._filter = SessionFilter()
        self._first_started = 0.0
        self._elapsed = 0.0
        self._samples: list[dict[str, float]] = []
        self._started = 0.0
        self._cpu_started = 0.0
        self._in_round = False
        self.result: report.Record | None = None
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
        self._lanes = [LaneViewer(totals) for _ in range(args.lanes)]
        for index, lane in enumerate(self._lanes):
            layout.addWidget(lane, index // columns, index % columns)
        self._scenes: list[list[Any]] = []
        self._sources: list[FileOverlaySource] = []
        self._timestamps: list[int] = []
        self._overlay_dir = tempfile.TemporaryDirectory(prefix="ax-devil-bench-overlays-")
        if args.decode:
            directory = Path(self._overlay_dir.name)
            self._timestamps = workload.write_overlay_files(directory, args.lanes, args.entities, _VARIANTS)
            self._sources = [
                FileOverlaySource(
                    directory / f"lane{n}.jsonl", ADFFrameV1DataProvider, "ADF_V1_FRAME", source_id=f"l{n}"
                )
                for n in range(args.lanes)
            ]
        else:
            self._scenes = [
                workload.moving_scenes(args.entities, _VARIANTS, seed, changing_scores=args.changing_scores)
                for seed in range(args.lanes)
            ]
        self._images = [workload.images(*_VIDEO, seed) for seed in range(args.lanes)]
        self.resize(*_VIDEO)
        self.show()
        QTimer.singleShot(200, self._submit)

    def _submit(self) -> None:
        self._totals.update(dict.fromkeys(self._totals, 0.0))
        self._started = perf_counter()
        if self._round == self._args.warmup:
            self._first_started = self._started
        self._cpu_started = process_time()
        self._in_round = True
        self._fallback.start()
        for index, lane in enumerate(self._lanes):
            lane.pending = True
            if self._args.interaction != "none" and self._round > 0:
                lane.set_viewport(self._viewport())
                continue
            overlay = CachedSceneOverlay(scene=self._scene(index), scene_filter=self._filter, catalog=self._catalog)
            image = self._images[index][self._round % 2]
            lane.display_frame(
                VideoFrameWithOverlays(
                    VideoFrame(image, self._round / 25, self._round),
                    VideoOverlayData(self._timed_generator(overlay), self._round / 25, self._round),
                )
            )

    def _scene(self, lane: int) -> Any:
        """Return this round's Scene, looked up and decoded through the overlay source with ``--decode``."""
        variant = self._round % _VARIANTS
        if not self._args.decode:
            return self._scenes[lane][variant]
        started = perf_counter()
        overlay = self._sources[lane].get_overlay_at_frame(
            FrameIdentifier(sequence_id=variant, timestamp_monotime_us=self._timestamps[variant])
        )
        self._totals["decode"] += (perf_counter() - started) * 1000
        if overlay is None:
            raise RuntimeError(f"Lane {lane} has no overlay for frame {variant}")
        return overlay.content

    def _viewport(self) -> NormalizedViewport:
        """Return the next view of a slow, endless pan or zoom, different from the previous round's.

        The pan stays inside what any window size allows: a view clamped to its previous value draws nothing new,
        so the round would never complete.
        """
        phase = math.sin(self._round / 8)
        if self._args.interaction == "zoom":
            return NormalizedViewport(zoom=2.5 + 1.5 * phase, pan_x=0.0, pan_y=0.0)
        return NormalizedViewport(zoom=2.0, pan_x=0.1 * phase, pan_y=0.07 * phase)

    def _timed_generator(
        self, overlay: CachedSceneOverlay
    ) -> Callable[[RenderContext, DrawingBuffer], PreparedDrawing]:
        def generate(context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
            started = perf_counter()
            overlay.filtered_scene()
            filtered = perf_counter()
            self._totals["filter"] += (filtered - started) * 1000
            drawing = overlay.prepare_drawing(context, buffer)
            self._totals["catalog"] += (perf_counter() - filtered) * 1000
            return drawing

        return generate

    def _composed(self, elapsed: float) -> None:
        self._totals["compose"] += elapsed
        if not self._in_round or any(lane.pending for lane in self._lanes):
            return
        if self._args.gpu_sync:
            rhi = self._lanes[0]._quick.quickWindow().rhi()
            if rhi is not None:
                started = perf_counter()
                rhi.finish()
                self._totals["gpu_wait"] += (perf_counter() - started) * 1000
        self._complete()

    def _uncomposed(self) -> None:
        if self._in_round and not any(lane.pending for lane in self._lanes):
            self._complete()
        elif self._in_round:
            self._fallback.start()

    def _complete(self) -> None:
        self._in_round = False
        self._fallback.stop()
        if self._round >= self._args.warmup:
            frame_total = sum(self._totals[stage] for stage in _FRAME_STAGES)
            wall = (perf_counter() - self._started) * 1000
            self._samples.append(
                {
                    **self._totals,
                    "frame_total": frame_total,
                    "wall": wall,
                    "rest": max(0.0, wall - frame_total),
                    "process_cpu": (process_time() - self._cpu_started) * 1000,
                }
            )
        self._round += 1
        if len(self._samples) >= self._args.frames:
            self._elapsed = perf_counter() - self._first_started
            self._finish()
            QTimer.singleShot(0, QApplication.quit)
        else:
            delay = max(0.0, self._started + 1 / self._args.fps - perf_counter()) if self._args.fps > 0 else 0.0
            QTimer.singleShot(round(delay * 1000), self._submit)

    def _finish(self) -> None:
        medians, p90s = report.stats(self._samples)
        self.result = {
            "env": report.environment(self._lanes[0]._quick.quickWindow()),
            "rounds": len(self._samples),
            "median_ms": medians,
            "p90_ms": p90s,
            "lane_fps": round(len(self._samples) / self._elapsed, 2),
            "process_cpu_ms": medians.pop("process_cpu"),
        }
        p90s.pop("process_cpu")

    def cleanup(self) -> None:
        """Release every lane's Qt resources."""
        self._compose_timer.remove()
        for source in self._sources:
            source.close()
        self._overlay_dir.cleanup()
        for lane in self._lanes:
            lane.frame_timer.remove()
            lane.cleanup()


def run(options: dict[str, Any]) -> report.Record:
    """Run one multi-lane measurement in this process and return its result."""
    args = SimpleNamespace(**options)
    setup_logging(console_only=True)
    set_metrics_enabled(False)
    app = QApplication([])
    totals = dict.fromkeys(_LANE_STAGES, 0.0)
    stages.install(totals)
    hidden = OverlayVisibility(frozenset(OverlayFeature(value) for value in args.hide_feature))
    catalog = workload.load_revision(args.catalog).specialize(hidden)
    widget = LaneBenchmark(args, catalog, totals)
    QTimer.singleShot(120000, app.quit)
    app.exec()
    widget.cleanup()
    widget.close()
    if widget.result is None:
        raise RuntimeError(f"Benchmark timed out after {len(widget._samples)} of {args.frames} measured rounds")
    return widget.result
