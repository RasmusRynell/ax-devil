"""Opening offline entries off the GUI thread and owning the sources until they are released."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from unittest.mock import MagicMock

import pytest
import shiboken6
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_viewer import offline_entry_media
from ax_devil.modules.video_viewer.offline_entry_media import (
    EntryMedia,
    EntryOpening,
    open_entry_media,
    release_media,
    run_in_background,
)
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineSession
from ax_devil.modules.workspace.core import (
    EntryLane,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistEntry,
    SeekableVideoContent,
)

_T = TypeVar("_T")


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: tmp_path / "cache")


def _entry(*paths: Path) -> PlaylistEntry:
    videos = [
        SeekableVideoContent(display_name=path.name, source_spec=FileVideoSourceSpec(path=path)) for path in paths
    ]
    return PlaylistEntry(
        lanes=tuple(
            EntryLane(display_name=video.display_name, video=video, default_considered=True) for video in videos
        ),
        default_considered=True,
    )


def _all_lanes(entry: PlaylistEntry) -> tuple[int, ...]:
    return tuple(range(len(entry.lanes)))


def _open_now(entry: PlaylistEntry) -> EntryMedia:
    return open_entry_media(
        entry,
        _all_lanes(entry),
        lambda _message: None,
        gui_thread=QThread.currentThread(),
        abandoned=threading.Event(),
    )


def _wait_released(qtbot: QtBot, sources: list[FileFrameSource], workers: set[threading.Thread]) -> None:
    qtbot.waitUntil(
        lambda: not any(shiboken6.isValid(source) for source in sources) and not any(w.is_alive() for w in workers)
    )


def _threads_started_by(action: Callable[[], _T]) -> tuple[_T, set[threading.Thread]]:
    baseline = set(threading.enumerate())
    result = action()
    return result, set(threading.enumerate()) - baseline


def test_background_work_reports_on_the_gui_thread_without_blocking_it(qtbot: QtBot) -> None:
    """The caller returns while the work runs; progress and the result arrive on the GUI thread, in order."""
    release = threading.Event()
    work_threads: list[QThread] = []
    delivered: list[tuple[str, QThread]] = []

    def work(report: Callable[[str], None]) -> str:
        work_threads.append(QThread.currentThread())
        report("halfway")
        assert release.wait(5)
        return "done"

    run_in_background(
        work,
        lambda result: delivered.append((result, QThread.currentThread())),
        on_progress=lambda message: delivered.append((message, QThread.currentThread())),
        name="test-background",
    )
    qtbot.waitUntil(lambda: len(delivered) == 1)
    release.set()
    qtbot.waitUntil(lambda: len(delivered) == 2)

    gui_thread = QThread.currentThread()
    assert work_threads[0] is not gui_thread
    assert delivered == [("halfway", gui_thread), ("done", gui_thread)]


def test_background_work_with_one_name_runs_one_at_a_time(qtbot: QtBot) -> None:
    """Work submitted under one name waits for the earlier work, so rapid requests never run side by side."""
    release_first = threading.Event()
    events: list[str] = []
    delivered: list[str] = []

    def first(_report: Callable[[str], None]) -> str:
        events.append("first started")
        assert release_first.wait(5)
        events.append("first finished")
        return "first"

    def second(_report: Callable[[str], None]) -> str:
        events.append("second started")
        return "second"

    run_in_background(first, delivered.append, name="test-serial")
    run_in_background(second, delivered.append, name="test-serial")
    qtbot.waitUntil(lambda: events == ["first started"])
    release_first.set()
    qtbot.waitUntil(lambda: len(delivered) == 2)

    assert events == ["first started", "first finished", "second started"]
    assert delivered == ["first", "second"]


def test_opened_media_lives_on_the_gui_thread_and_shares_one_source_per_video(
    qtbot: QtBot, video_file_factory: Callable[[float, int], Path]
) -> None:
    """Lanes over the same video share its source, and every source is handed to the GUI thread ready for use."""
    path = video_file_factory(0.1, 10)
    delivered: list[EntryMedia] = []
    opening = EntryOpening(on_status=lambda _message: None, on_opened=delivered.append)

    video = SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=path))
    shared = PlaylistEntry(
        lanes=tuple(EntryLane(display_name=name, video=video, default_considered=True) for name in ("a", "b")),
        default_considered=True,
    )

    opening.start(shared, (0, 1))
    qtbot.waitUntil(lambda: len(delivered) == 1)
    media = delivered[0]
    try:
        assert media.error is None
        assert len(media.video_sources) == 1
        assert [lane.source_index for lane in media.lanes] == [0, 0]
        assert media.video_sources[0].thread() is QThread.currentThread()
        assert media.video_sources[0].get_total_frames() == 1
    finally:
        media.close()
        media.delete_later()


def test_failed_open_keeps_what_it_opened_for_release(
    qtbot: QtBot, tmp_path: Path, video_file_factory: Callable[[float, int], Path]
) -> None:
    """An unreadable second video is reported, and the first video's source is still owned and released once."""
    path = video_file_factory(0.1, 10)
    entry = _entry(path, tmp_path / "missing.mp4")

    media, workers = _threads_started_by(lambda: _open_now(entry))

    assert media.error is not None
    assert len(media.video_sources) == 1
    sources = list(media.video_sources)
    release_media(media)
    _wait_released(qtbot, sources, workers)
    assert sources[0]._frame_delivery is None


def test_abandoned_opening_opens_nothing_more_and_releases_what_it_opened(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, video_file_factory: Callable[[float, int], Path]
) -> None:
    """Abandoning mid-open stops before the next video, never delivers, and releases the video already opening."""
    path = video_file_factory(0.1, 10)
    opening_started = threading.Event()
    finish_opening = threading.Event()
    opened: list[FileFrameSource] = []
    original_open = offline_entry_media.create_frame_source

    def slow_open(video: SeekableVideoContent) -> FileFrameSource:
        opening_started.set()
        assert finish_opening.wait(5)
        source = original_open(video)
        opened.append(source)
        return source

    monkeypatch.setattr(offline_entry_media, "create_frame_source", slow_open)
    delivered: list[EntryMedia] = []
    opening = EntryOpening(on_status=lambda _message: None, on_opened=delivered.append)
    baseline = set(threading.enumerate())

    opening.start(_entry(path, video_file_factory(0.2, 10)), (0, 1))
    qtbot.waitUntil(opening_started.is_set)
    opening.abandon()
    finish_opening.set()

    qtbot.waitUntil(lambda: len(opened) == 1)
    _wait_released(qtbot, opened, set(threading.enumerate()) - baseline)
    assert len(opened) == 1
    assert opened[0]._frame_delivery is None
    assert delivered == []


@pytest.mark.parametrize("abandon_during", ["video", "overlay"])
def test_abandoned_opening_skips_analysis_after_a_blocking_open(
    monkeypatch: pytest.MonkeyPatch, abandon_during: str
) -> None:
    """Abandoning while a source opens skips the timing and history analysis that would delay the next entry."""
    abandoned = threading.Event()
    source = MagicMock()
    overlay_source = MagicMock()

    def open_video(_video: SeekableVideoContent) -> MagicMock:
        if abandon_during == "video":
            abandoned.set()
        return source

    def open_overlay(_overlay: OverlayContent, *, frame_timeline: object) -> tuple[MagicMock, MagicMock]:
        if abandon_during == "overlay":
            abandoned.set()
        return overlay_source, MagicMock()

    monkeypatch.setattr(offline_entry_media, "create_frame_source", open_video)
    monkeypatch.setattr(offline_entry_media, "create_overlay_source", open_overlay)
    overlay = OverlayContent(
        display_name="overlay",
        source_spec=FileOverlaySourceSpec(path=Path("/tmp/overlay.txt"), handler_type="TEST_FILE"),
    )
    video = SeekableVideoContent(
        display_name="video", source_spec=FileVideoSourceSpec(path=Path("/tmp/video.mp4")), overlays=(overlay,)
    )
    entry = PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True)

    media = open_entry_media(
        entry, _all_lanes(entry), lambda _message: None, gui_thread=QThread.currentThread(), abandoned=abandoned
    )

    assert media.video_sources == [source]
    assert media.overlay_sources() == ([overlay_source] if abandon_during == "overlay" else [])
    if abandon_during == "video":
        source.get_timing_profile.assert_not_called()
    overlay_source.scene_history.assert_not_called()
    overlay_source.analyze_alignment.assert_not_called()


def test_session_cleanup_releases_sources_off_the_gui_thread(
    qtbot: QtBot, video_file_factory: Callable[[float, int], Path], render_catalog_manager: SceneRenderCatalogManager
) -> None:
    """Replacing an entry closes its sources in the background and deletes them on the GUI thread."""
    parent = QWidget()
    qtbot.addWidget(parent)
    media, workers = _threads_started_by(lambda: _open_now(_entry(video_file_factory(0.1, 10))))
    sources = list(media.video_sources)
    session = OfflineSession.build(parent, media, render_catalog_manager=render_catalog_manager)

    session.cleanup()

    _wait_released(qtbot, sources, workers)
    assert sources[0]._frame_delivery is None


def test_blocking_session_cleanup_closes_sources_before_returning(
    qtbot: QtBot, video_file_factory: Callable[[float, int], Path], render_catalog_manager: SceneRenderCatalogManager
) -> None:
    """A viewer going away closes its sources before cleanup returns, so no source worker outlives it."""
    parent = QWidget()
    qtbot.addWidget(parent)
    media, workers = _threads_started_by(lambda: _open_now(_entry(video_file_factory(0.1, 10))))
    sources = list(media.video_sources)
    session = OfflineSession.build(parent, media, render_catalog_manager=render_catalog_manager)

    session.cleanup(blocking=True)

    assert sources[0]._frame_delivery is None
    _wait_released(qtbot, sources, workers)


def test_viewer_opens_a_real_video_without_waiting_for_it(
    qtbot: QtBot, video_file_factory: Callable[[float, int], Path], render_catalog_manager: SceneRenderCatalogManager
) -> None:
    """Attaching the viewer returns at once; the entry appears once its video has opened in the background."""
    path = video_file_factory(0.1, 10)
    video = SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=path))
    widget = OfflineVideoViewerWidget(video, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(widget)

    widget.on_workspace_attached()

    assert widget._runtime is None
    assert widget._loading_indicator is not None
    qtbot.waitUntil(lambda: widget._runtime is not None)
    assert widget._loading_indicator is None
    widget.cleanup()
