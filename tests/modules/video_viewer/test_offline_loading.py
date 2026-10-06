"""Resource ownership while offline sessions are being constructed."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.data_sources import FileFrameSource, FileOverlaySource
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.synchronization import TimestampFallbackPolicy
from ax_devil.modules.video_viewer.offline_viewer_runtime import LoadingCancelled, OfflineSession
from ax_devil.modules.workspace import EntryLane, FileVideoSourceSpec, PlaylistEntry, SeekableVideoContent


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: tmp_path / "cache")


@pytest.mark.parametrize("cancel_during_open", [True, False])
def test_cancelled_open_closes_real_source(
    qtbot: QtBot, video_file_factory: Callable[[float, int], Path], cancel_during_open: bool
) -> None:
    """Cancellation disposes results both during work and at the final handoff."""
    path = video_file_factory(0.1, 10)
    cancelled = threading.Event()
    started = threading.Event()
    release = threading.Event()
    sources: list[FileFrameSource] = []
    workers: list[threading.Thread] = []
    baseline = set(threading.enumerate())

    def open_source() -> FileFrameSource:
        started.set()
        assert release.wait(5)
        source = FileFrameSource(str(path))
        sources.append(source)
        workers.extend(thread for thread in threading.enumerate() if thread not in baseline)
        if not cancel_during_open:
            cancelled.set()
        return source

    timer = QTimer()

    def release_open() -> None:
        if started.is_set():
            if cancel_during_open:
                cancelled.set()
            release.set()
            timer.stop()

    timer.timeout.connect(release_open)
    timer.start(1)
    try:
        with pytest.raises(LoadingCancelled):
            OfflineSession._run_in_background(
                open_source,
                cancel_loading=cancelled,
                dispose=lambda source: OfflineSession._dispose_sources([source], []),
            )
        assert len(sources) == 1
        assert sources[0]._frame_delivery is None
        qtbot.waitUntil(lambda: all(not worker.is_alive() for worker in workers))
    finally:
        release.set()
        timer.stop()
        for source in sources:
            source.stop()


def test_pre_cancelled_loading_does_not_start_work(qtbot: QtBot) -> None:
    """An already cancelled viewer must not open another source."""
    cancelled = threading.Event()
    cancelled.set()
    builder = MagicMock()
    with pytest.raises(LoadingCancelled):
        OfflineSession._run_in_background(builder, cancel_loading=cancelled)
    builder.assert_not_called()


@pytest.mark.parametrize("cancel", [False, True])
def test_partial_build_releases_shared_video_and_overlays(
    qtbot: QtBot,
    video_file_factory: Callable[[float, int], Path],
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
    cancel: bool,
) -> None:
    """A later failed or cancelled lane rolls back each already owned source once."""
    path = video_file_factory(0.1, 10)
    video = SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=path))
    entry = PlaylistEntry(
        lanes=tuple(EntryLane(display_name=str(i), video=video, default_considered=True) for i in range(3)),
        default_considered=True,
    )
    parent = QWidget()
    qtbot.addWidget(parent)
    cancelled = threading.Event()
    sources: list[FileFrameSource] = []
    overlays: list[MagicMock] = []
    baseline = set(threading.enumerate())
    workers: list[threading.Thread] = []
    original_open = OfflineSession._create_frame_source

    def open_video(content: SeekableVideoContent) -> FileFrameSource:
        source = original_open(content)
        sources.append(source)
        workers.extend(thread for thread in threading.enumerate() if thread not in baseline)
        return source

    def open_overlay(*args: object, **kwargs: object) -> tuple[MagicMock, None]:
        if len(overlays) == 2:
            if cancel:
                cancelled.set()
            else:
                raise ValueError("third lane failed")
        overlay = MagicMock(spec=FileOverlaySource)
        overlay.diagnostics_id = "test-overlay"
        overlay.get_filter_config.return_value = None
        overlay.analyze_alignment.return_value = None
        overlay.get_timestamp_fallback_policy.return_value = TimestampFallbackPolicy()
        overlays.append(overlay)
        return overlay, None

    monkeypatch.setattr(OfflineSession, "_create_frame_source", staticmethod(open_video))
    monkeypatch.setattr(OfflineSession, "_create_overlay_source", staticmethod(open_overlay))
    try:
        with pytest.raises(LoadingCancelled if cancel else ValueError):
            OfflineSession.build(
                parent,
                entry,
                lane_included=lambda _: True,
                cancel_loading=cancelled,
                render_catalog_manager=render_catalog_manager,
            )
        assert len(sources) == 1
        assert sources[0]._frame_delivery is None
        assert len(overlays) == (3 if cancel else 2)
        for overlay in overlays:
            overlay.close.assert_called_once_with()
        qtbot.waitUntil(lambda: all(not worker.is_alive() for worker in workers))
    finally:
        for source in sources:
            source.stop()


def test_second_video_failure_closes_first_video(
    qtbot: QtBot,
    tmp_path: Path,
    video_file_factory: Callable[[float, int], Path],
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    """An unreadable second video cannot leave the first video's worker alive."""
    path = video_file_factory(0.1, 10)
    videos = [
        SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=video_path))
        for name, video_path in (("valid", path), ("missing", tmp_path / "missing.mp4"))
    ]
    entry = PlaylistEntry(
        lanes=tuple(
            EntryLane(display_name=video.display_name, video=video, default_considered=True) for video in videos
        ),
        default_considered=True,
    )
    parent = QWidget()
    qtbot.addWidget(parent)
    sources: list[FileFrameSource] = []
    workers: list[threading.Thread] = []
    baseline = set(threading.enumerate())
    original_open = OfflineSession._create_frame_source

    def open_video(content: SeekableVideoContent) -> FileFrameSource:
        source = original_open(content)
        sources.append(source)
        workers.extend(thread for thread in threading.enumerate() if thread not in baseline)
        return source

    monkeypatch.setattr(OfflineSession, "_create_frame_source", staticmethod(open_video))
    try:
        with pytest.raises(ValueError):
            OfflineSession.build(
                parent,
                entry,
                lane_included=lambda _: True,
                cancel_loading=threading.Event(),
                render_catalog_manager=render_catalog_manager,
            )
        assert len(sources) == 1
        assert sources[0]._frame_delivery is None
        qtbot.waitUntil(lambda: all(not worker.is_alive() for worker in workers))
    finally:
        for source in sources:
            source.stop()


def test_session_shutdown_finishes_after_loading_cancelled(
    qtbot: QtBot,
    video_file_factory: Callable[[float, int], Path],
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    """A cancellation flag cannot bypass source shutdown or Qt disposal."""
    path = video_file_factory(0.1, 10)
    video = SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=path))
    parent = QWidget()
    qtbot.addWidget(parent)
    cancelled = threading.Event()
    runtime = OfflineSession.build(
        parent,
        PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),
        lane_included=lambda _: True,
        cancel_loading=cancelled,
        render_catalog_manager=render_catalog_manager,
    )
    source = runtime.get_primary_video_source()
    assert source is not None
    cancelled.set()
    try:
        with qtbot.waitSignal(source.destroyed):
            runtime.cleanup()
        assert source._frame_delivery is None
    finally:
        source.stop()
