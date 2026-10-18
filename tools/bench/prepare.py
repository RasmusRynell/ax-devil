"""Catalog evaluation and drawing preparation without a GPU: one scene through ``DrawingBuffer`` per sample.

``total`` is evaluating the catalog and finishing the buffer; the stages in ``stages.py`` are parts of it and ``rest``
is the remainder (catalog evaluation itself). ``--profile N`` also prints the N most expensive functions by own time.
"""

from __future__ import annotations

import cProfile
import dataclasses
import pstats
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from PySide6.QtWidgets import QApplication

from ax_devil.modules.diagnostics.metrics_gate import set_metrics_enabled
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings
from ax_devil.modules.video_player.engine.render_context import RenderContext

from . import report, stages, workload

_TARGET = (960, 540)


def run(options: dict[str, Any]) -> report.Record:
    """Measure preparation of one scene in this process and return its result."""
    args = SimpleNamespace(**options)
    set_metrics_enabled(False)
    _app = QApplication([])  # Must outlive the measurement.
    totals: dict[str, float] = {}
    stages.install(totals)
    catalog = workload.load_revision(args.catalog).catalog
    scene = workload.moving_scenes(args.entities, 1, 0)[0]
    context = RenderContext.create(*_TARGET)
    settings = DrawingSettings.for_context(context)
    if "hardware" in {field.name for field in dataclasses.fields(DrawingSettings)}:
        older: dict[str, Any] = {"hardware": True}  # Older refs prepare for the GPU only when asked.
        settings = dataclasses.replace(settings, **older)

    def frame() -> None:
        buffer = DrawingBuffer(settings)
        catalog.render_scene(scene, context, buffer)
        buffer.finish()

    for _ in range(args.warmup):
        frame()
    samples = []
    for _ in range(args.frames):
        totals.update(dict.fromkeys(totals, 0.0))
        started = perf_counter()
        frame()
        total = (perf_counter() - started) * 1000
        samples.append({"total": total, **totals, "rest": total - sum(totals.values())})
    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(args.frames):
            frame()
        profiler.disable()
        pstats.Stats(profiler).sort_stats("tottime").print_stats(args.profile)
    medians, p90s = report.stats(samples)
    result = {
        "env": report.environment(),
        "rounds": len(samples),
        "median_ms": medians,
        "p90_ms": p90s,
        "lane_fps": None,
        "process_cpu_ms": None,
    }
    return result
