"""Measure viewer CPU submission/render completion on the actual Qt desktop backend.

This measures process CPU consumed through render submission and separately reports
wall-clock delivery (which includes event-loop/vsync waiting), not GPU or screen latency.
The ordinary diagnostics intentionally measure a smaller GUI-preparation scope.
"""

from __future__ import annotations

import argparse
import json
from statistics import median
from time import perf_counter, process_time

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.engine.data_types import (
    VideoFrame,
    VideoFrameWithOverlays,
    VideoOverlayData,
)
from ax_devil.modules.video_player.engine.drawing import DrawingStyle, Paint, Points
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer

logger = get_logger(__name__)


class BenchmarkViewer(VideoFrameRenderer):
    """Exercise the production viewer and measure completion without framebuffer readback."""

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self._args = args
        self._number = 0
        self._prepared = False
        self._started: float | None = None
        self._samples: list[float] = []
        self._wall_samples: list[float] = []
        self._cpu_started = 0.0
        self._stage_start = 0.0
        self._stages = {"gui_prepare": 0.0, "scene_sync": 0.0, "render_submission": 0.0}
        self._stage_samples: dict[str, list[float]] = {key: [] for key in self._stages}
        self._fixed_image = QImage(1280, 720, QImage.Format.Format_RGB32)
        self._fixed_image.fill(QColor(20, 25, 30))
        self._static: tuple[DrawingSettings, PreparedDrawing] | None = None
        self._submit = QTimer(self)
        self._submit.setSingleShot(True)
        self._submit.timeout.connect(self.next_frame)
        window = self._quick.quickWindow()
        window.beforeSynchronizing.connect(self._begin_stage)
        window.afterSynchronizing.connect(self._end_sync)
        window.beforeRendering.connect(self._begin_stage)
        window.afterRendering.connect(self._end_render)
        window.afterRendering.connect(self.completed)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(args.width, args.height)
        self.show()
        self._submit.start(0)

    def _drawing(self, frame: int, buffer: DrawingBuffer) -> PreparedDrawing:
        style = DrawingStyle(pen_r=60, pen_g=180, pen_b=230, pen_width=0.0015, font_size=0.016)
        paint = Paint.of(style)
        boxes: list[tuple[float, float, float, float]] = []
        circles: list[tuple[float, float, float]] = []
        lines: list[tuple[float, float, float, float]] = []
        polygons: list[Points] = []
        polylines: list[Points] = []
        labels: list[tuple[float, float, str]] = []
        for index in range(self._args.primitives):
            x = ((index * 17 + frame) % 900) / 1000
            y = ((index * 31 + frame) % 800) / 1000
            width = 0.06 + (frame % 10) * 0.001 if self._args.changing_content else 0.06
            kind = index % 5 if self._args.mixed else 0
            if kind == 0:
                boxes.append((x, y, width, 0.1))
            elif kind == 1:
                circles.append((x, y, width / 2))
            elif kind == 2:
                lines.append((x, y, x + width, y + 0.1))
            elif kind == 3:
                polygons.append(((x, y), (x + width, y), (x + width / 2, y + 0.1)))
            else:
                polylines.append(((x, y), (x + width, y + 0.05), (x, y + 0.1)))
            if self._args.text:
                label = f"Object {index}: {frame % 10}" if self._args.changing_content else f"Object {index}"
                labels.append((x, y, label))
        if boxes:
            box = np.array(boxes, dtype=np.float64)
            buffer.boxes(box[:, 0], box[:, 1], box[:, 2], box[:, 3], paint)
        if circles:
            circle = np.array(circles, dtype=np.float64)
            buffer.circles(circle[:, 0], circle[:, 1], circle[:, 2], paint)
        if lines:
            line = np.array(lines, dtype=np.float64)
            buffer.lines(line[:, 0], line[:, 1], line[:, 2], line[:, 3], paint)
        if polygons:
            buffer.polygons(polygons, paint)
        if polylines:
            buffer.polylines(polylines, paint)
        if labels:
            xs, ys, texts = zip(*labels)
            buffer.texts(np.array(xs), np.array(ys), list(texts), ["baseline"] * len(texts), paint)
        return buffer.finish()

    def next_frame(self) -> None:
        """Submit a complete frame without timing source/image generation."""
        image = QImage(1280, 720, QImage.Format.Format_RGB32)
        image.fill(QColor(20 + self._number % 5, 25, 30))
        if self._args.fixed_image:
            image = self._fixed_image

        def generate(_context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
            if self._args.moving:
                return self._drawing(self._number, buffer)
            if self._static is None or self._static[0] != buffer.settings:
                self._static = buffer.settings, self._drawing(0, buffer)
            return self._static[1]

        self._stages = dict.fromkeys(self._stages, 0.0)
        frame = VideoFrameWithOverlays(
            VideoFrame(image, self._number / 30, self._number),
            VideoOverlayData(generate, self._number / 30),
        )
        self._prepared = False
        self._started = perf_counter()
        self._cpu_started = process_time()
        self.display_frame(frame)

    def _prepare_quick_frame(self) -> None:
        started = process_time()
        super()._prepare_quick_frame()
        self._stages["gui_prepare"] += (process_time() - started) * 1000
        self._prepared = True

    def _begin_stage(self) -> None:
        self._stage_start = process_time()

    def _end_sync(self) -> None:
        if self._prepared:
            self._stages["scene_sync"] += (process_time() - self._stage_start) * 1000

    def _end_render(self) -> None:
        if self._prepared:
            self._stages["render_submission"] += (process_time() - self._stage_start) * 1000

    def completed(self) -> None:
        """Capture one sample per submitted frame and queue the next event-loop turn."""
        if self._started is None or not self._prepared:
            return
        elapsed = (process_time() - self._cpu_started) * 1000
        wall_elapsed = (perf_counter() - self._started) * 1000
        self._started = None
        if self._number >= 10:
            self._samples.append(elapsed)
            self._wall_samples.append(wall_elapsed)
            for key, value in self._stages.items():
                self._stage_samples[key].append(value)
        self._number += 1
        if len(self._samples) >= self._args.frames:
            samples = sorted(self._samples)
            logger.warning(
                json.dumps(
                    {
                        "graphics_api": str(self._quick.quickWindow().rendererInterface().graphicsApi()),
                        "boxes": self._args.primitives,
                        "viewport": [self.width(), self.height()],
                        "text": self._args.text,
                        "moving": self._args.moving,
                        "changing_content": self._args.changing_content,
                        "mixed": self._args.mixed,
                        "fixed_image": self._args.fixed_image,
                        "stage_cpu_median_ms": {key: median(values) for key, values in self._stage_samples.items()},
                        "frames": len(samples),
                        "process_cpu_median_ms": median(samples),
                        "wall_submission_median_ms": median(self._wall_samples),
                        "process_cpu_p95_ms": samples[min(len(samples) - 1, int(len(samples) * 0.95))],
                    }
                )
            )
            QTimer.singleShot(0, self._finish)
        else:
            self._submit.start(0)

    def _finish(self) -> None:
        self.cleanup()
        QApplication.quit()


def main() -> None:
    """Run one real-window renderer benchmark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--software", action="store_true")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--primitives", type=int, default=300)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--moving", action="store_true")
    parser.add_argument("--text", action="store_true")
    parser.add_argument(
        "--changing-content",
        action="store_true",
        help="Change shape dimensions and label contents",
    )
    parser.add_argument(
        "--mixed",
        action="store_true",
        help="Include circles, lines, polygons, and polylines",
    )
    parser.add_argument(
        "--fixed-image",
        action="store_true",
        help="Reuse the video texture to isolate overlay updates",
    )
    args = parser.parse_args()
    args.moving = args.moving or args.changing_content
    if args.frames < 1 or args.primitives < 0 or min(args.width, args.height) < 1:
        parser.error("frames/dimensions must be positive and primitives nonnegative")
    app = QApplication([])
    preference = GraphicsAcceleration.OFF if args.software else GraphicsAcceleration.AUTO
    preference.configure()
    widget = BenchmarkViewer(args)
    QTimer.singleShot(60000, app.quit)
    app.exec()
    widget.close()
    if len(widget._samples) != args.frames:
        raise RuntimeError(f"Benchmark timed out after {len(widget._samples)} of {args.frames} measured frames")


if __name__ == "__main__":
    main()
