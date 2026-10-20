"""Render a catalog's example sheets to PNG as the viewer draws them, and compare two renderings."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from . import report, workload

_DIFFERENT = 64  # A pixel differs when any channel differs by more than this.


def run(options: dict[str, Any]) -> report.Record:
    """Render every sheet of the catalog into ``out`` and report the time each took."""
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from ax_devil.modules.catalog_viewer.sheet_drawing import sheet_frame
    from ax_devil.modules.catalog_viewer.sheets import sheets
    from ax_devil.modules.video_player.engine.quick.image_renderer import FrameImageRenderer

    args = SimpleNamespace(**options)
    _app = QApplication([])  # Must outlive the measurement.
    revision = workload.load_revision(args.catalog)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.png"):  # Sheets of an earlier run must not pass for this one's.
        stale.unlink()
    renderer = FrameImageRenderer()
    timings: dict[str, float] = {}
    try:
        for sheet in sheets(revision.document):
            started = perf_counter()
            image = renderer.render_frame(sheet_frame(sheet, revision.catalog))
            timings[sheet.key] = (perf_counter() - started) * 1000
            if not image.save(str(out / f"{sheet.key}.png")):
                raise RuntimeError(f"Could not write {out / sheet.key}.png")
    finally:
        renderer.cleanup()
        delete(renderer)  # Destroy native nodes while their Python texture adapters are still alive.
    medians, p90s = report.stats([{**timings, "total": sum(timings.values())}])
    return {
        "env": report.environment(),
        "rounds": len(timings),
        "median_ms": medians,
        "p90_ms": p90s,
        "lane_fps": None,
        "process_cpu_ms": None,
    }


def pixel_differences(base: Path, head: Path) -> dict[str, str]:
    """Describe, per sheet in either folder, how much the two renderings differ, or which side lacks it."""
    import numpy as np
    from PySide6.QtGui import QImage

    def load(path: Path) -> Any:
        image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
        raw = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
        return raw[:, : image.width() * 4].reshape(image.height(), image.width(), 4).astype(int)

    result: dict[str, str] = {}
    for name in sorted({path.name for path in (*base.glob("*.png"), *head.glob("*.png"))}):
        if not (base / name).exists() or not (head / name).exists():
            result[Path(name).stem] = f"only in {'head' if (head / name).exists() else 'base'}"
            continue
        before, after = load(base / name), load(head / name)
        if before.shape != after.shape:
            result[Path(name).stem] = "size changed"
        else:
            percent = float((np.abs(before - after).max(axis=2) > _DIFFERENT).mean() * 100)
            result[Path(name).stem] = f"{percent:6.2f}% of pixels differ"
    return result
