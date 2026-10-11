"""Application-specific demo recipes; imported only inside the isolated worker."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListView

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.chrome.theme import setup_theme
from ax_devil.modules.plugin_system import ApplicationPluginLoader
from ax_devil.modules.scene.rendering import create_scene_render_catalog_manager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger, setup_logging
from ax_devil.modules.settings.paths import DEFAULT_CONFIG_PATH
from ax_devil.modules.settings.playback_settings import VideoCacheBudget
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.video_viewer.export.encoder import CompressionPreset, VideoEncoder
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineSession
from ax_devil.modules.workspace.core import OverlayFile, VideoItem

logger = get_logger(__name__)


def frame_schedule(timestamps: Sequence[int], start: float, count: int, fps: int) -> list[int]:
    """Sample the actual source timeline; refuse a take that would freeze beyond its end."""
    if not timestamps:
        raise ValueError("The source has no frames")
    times = [round((start + index / fps) * 1_000_000) + timestamps[0] for index in range(count)]
    if times[-1] > timestamps[-1]:
        raise ValueError("Take extends beyond the source; reduce --frames or --start")
    return [max(0, bisect.bisect_right(timestamps, timestamp) - 1) for timestamp in times]


def simulate_detection(source: Path, destination: Path) -> None:
    """Make a seeded, explicitly synthetic UVG-VCM detector lane without changing source annotations."""
    data: dict[str, Any] = json.loads(source.read_text())
    rng = random.Random(11)
    result: dict[str, Any] = {"version": data.get("version", "1.0")}
    for key in sorted((key for key in data if key != "version"), key=int):
        detections = []
        for original in data[key]:
            if rng.random() < 0.18:
                continue
            item = dict(original)
            item.pop("polygon", None)
            width = float(item["x_max"]) - float(item["x_min"])
            height = float(item["y_max"]) - float(item["y_min"])
            dx, dy = rng.uniform(-0.08, 0.08) * width, rng.uniform(-0.08, 0.08) * height
            for field, shift in (("x_min", dx), ("x_max", dx), ("y_min", dy), ("y_max", dy)):
                item[field] = max(0.0, min(1.0, float(item[field]) + shift))
            item["track_id"] = 40 + int(item["track_id"]) * 3
            detections.append(item)
        result[key] = detections
    destination.write_text(json.dumps(result))


def wait_until(predicate: Callable[[], bool], description: str, timeout: float = 60) -> None:
    """Process Qt events until a state is ready, with an actionable failure deadline."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {description}")
        QTest.qWait(5)


def grab(window: MainWindow, runtime: OfflineSession) -> QImage:
    """Synchronously prepare/read back every Quick surface before capturing the whole widget tree."""
    QApplication.processEvents()
    for lane in runtime.lanes:
        viewport = lane.display.viewport
        viewport._prepare_quick_frame()
        if viewport._quick.grabFramebuffer().isNull():
            raise RuntimeError("Qt Quick returned an empty frame")
    image: QImage = window.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    if image.isNull():
        raise RuntimeError("Application capture returned an empty image")
    return image


def wipe(first: QImage, second: QImage, area: QRect, progress: float) -> QImage:
    """Reveal the next catalog within the video viewport, preserving the application chrome."""
    image = first.copy()
    width = round(area.width() * (0.5 - 0.5 * math.cos(math.pi * progress)))
    region = QRect(area.x(), area.y(), width, area.height())
    painter = QPainter(image)
    painter.drawImage(region, second, region)
    painter.setPen(QColor("white"))
    painter.drawLine(region.right(), area.top(), region.right(), area.bottom())
    painter.end()
    return image


def _item(args: argparse.Namespace) -> VideoItem:
    """Describe the video to open; a comparison puts two overlays on it, one lane each, named after their files."""
    paths: list[Path] = [] if args.overlay is None else [args.overlay]
    if args.simulate_det:
        simulated = args.output / "DET (simulated).json"
        simulate_detection(args.overlay, simulated)
        paths.insert(0, simulated)
    elif args.compare_overlay is not None:
        paths.append(args.compare_overlay)
    return VideoItem(video=args.video, overlays=tuple(OverlayFile(path, args.handler) for path in paths))


def capture(args: argparse.Namespace) -> None:
    """Capture a complete take through the real Workspace and renderer, then release all workers."""
    setup_logging(console_only=True, console_log_level="INFO")
    config = ConfigManager()
    config.set_config_path(DEFAULT_CONFIG_PATH, create_if_missing=True)
    config.activate_storage()
    config.ensure_storage_directories()
    ApplicationPluginLoader.load_all()
    app = QApplication([])
    settings = GlobalSettings()
    settings.video_cache_budget = VideoCacheBudget.from_config(256)
    settings.graphics_acceleration.configure()
    setup_theme(app, "dark", settings.text_size.body_px)
    shortcuts = ShortcutManager()
    shortcuts.register_defaults()
    catalogs = create_scene_render_catalog_manager()
    catalog_paths = [catalogs.built_in_catalog_path().parent / f"{name}.json" for name in args.catalogs]
    for path in catalog_paths:
        catalogs.load_catalog(path)
    catalogs.set_default_catalog(catalog_paths[0])
    window = MainWindow(shortcuts, catalogs, use_custom_frame=True)
    session = window._workspace_session
    encoder: VideoEncoder | None = None
    partial = args.output / "raw.partial.mp4"
    try:
        window.show()
        QApplication.processEvents()
        window.resize(args.width, args.height)
        window.move(0, 0)
        session.add_items([_item(args)])
        wait_until(lambda: session.focused_offline_viewer() is not None, "the video to open")
        viewer = session.focused_offline_viewer()
        assert viewer is not None
        wait_until(lambda: viewer._runtime is not None, "video and overlays to open")
        runtime = viewer._runtime
        assert runtime is not None
        runtime.pause_playback()
        source = runtime.get_primary_video_source()
        assert source is not None
        schedule = frame_schedule(source.get_frame_timestamps_us(), args.start, args.frames, args.fps)
        first_lane = runtime.lanes[0]
        inspector_open = False
        # Capture only Qt-owned content. Park the private display's pointer over the title bar.
        QTest.mouseMove(window, QPoint(10, 10))
        QTest.qWait(300)  # Finish startup chrome animations before recording.
        if window.size().width() != args.width or window.size().height() != args.height:
            raise ValueError(f"Window minimum size exceeds {args.width}x{args.height}; choose larger dimensions")
        encoder = VideoEncoder(
            partial, width=args.width, height=args.height, fps=args.fps, compression=CompressionPreset.lossless()
        )
        active_catalog = catalog_paths[0]
        for index, frame in enumerate(schedule):
            runtime.jump_to_frame(frame)
            wait_until(
                lambda: all(
                    lane.display.viewport._video_frame is not None
                    and lane.display.viewport._video_frame.frame.frame_id == frame
                    for lane in runtime.lanes
                ),
                f"source frame {frame}",
            )
            if (
                args.recipe != "compare"
                and args.inspector_at >= 0
                and index / args.fps >= args.inspector_at
                and not inspector_open
            ):
                first_lane.display.set_side_panel_open(True)
                QTest.qWait(250)  # Let the opening animation finish; source-frame readiness is checked separately.
                if first_lane.tools_panel is not None:
                    view = first_lane.tools_panel.entity_list.findChild(QListView)
                    if view is not None and view.model().rowCount() > 0:
                        rectangle = view.visualRect(view.model().index(0, 0))
                        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rectangle.center())
                QTest.mouseMove(window, QPoint(10, 10))
                inspector_open = True
            label = ""
            if args.recipe == "catalogs":
                position = index * len(catalog_paths) / len(schedule)
                segment = min(int(position), len(catalog_paths) - 1)
                selected = catalog_paths[segment]
                if active_catalog != selected:
                    catalogs.apply_to_all(selected)
                    active_catalog = selected
                label = selected.stem.capitalize()
            image = grab(window, runtime)
            if args.recipe == "catalogs" and segment + 1 < len(catalog_paths) and position - segment > 0.7:
                next_catalog = catalog_paths[segment + 1]
                catalogs.apply_to_all(next_catalog)
                second = grab(window, runtime)
                viewport = first_lane.display.viewport
                area = QRect(viewport.mapTo(window, QPoint()), viewport.size())
                image = wipe(image, second, area, (position - segment - 0.7) / 0.3)
                catalogs.apply_to_all(selected)
            if label:
                painter = QPainter(image)
                painter.fillRect(QRect(20, args.height - 48, 210, 30), QColor("#101726"))
                painter.setPen(QColor("white"))
                painter.drawText(
                    QRect(30, args.height - 48, 190, 30), Qt.AlignmentFlag.AlignVCenter, f"Catalog: {label}"
                )
                painter.end()
            encoder.write_frame(image, timestamp_us=index * 1_000_000 / args.fps)
            if index == 0:
                image.save(str(args.output / "raw-preview.png"))
            if index % args.fps == 0:
                logger.info(f"Captured {index + 1}/{len(schedule)} frames")
        encoder.finish()
        encoder = None
        partial.rename(args.output / "raw.mp4")
        metadata = vars(args).copy()
        for key in ("video", "overlay", "compare_overlay", "output"):
            value = getattr(args, key)
            metadata[key] = str(value) if value is not None else None
        metadata.update(source_frames=schedule, capture="frame-stepped", audio=False)
        (args.output / "take.json").write_text(json.dumps(metadata, indent=2))
    finally:
        try:
            if encoder is not None:
                encoder.finish()
        finally:
            session.cleanup()
            window.close()
            QApplication.processEvents()
