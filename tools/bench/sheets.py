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

    from ax_devil.modules.catalog_viewer.sheets import sheet_frame, sheets
    from ax_devil.modules.video_player.engine.quick.image_renderer import FrameImageRenderer

    args = SimpleNamespace(**options)
    _app = QApplication([])  # Must outlive the measurement.
    revision = workload.load_revision(args.catalog)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
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


def pixel_differences(base: Path, head: Path) -> dict[str, float | None]:
    """Return, per sheet, the percentage of pixels that differ clearly between two folders (None: size changed)."""
    import numpy as np
    from PySide6.QtGui import QImage

    def load(path: Path) -> Any:
        image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
        raw = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
        return raw[:, : image.width() * 4].reshape(image.height(), image.width(), 4).astype(int)

    result: dict[str, float | None] = {}
    for path in sorted(base.glob("*.png")):
        before, after = load(path), load(head / path.name)
        same_size = before.shape == after.shape
        result[path.stem] = float((np.abs(before - after).max(axis=2) > _DIFFERENT).mean() * 100) if same_size else None
    return result
