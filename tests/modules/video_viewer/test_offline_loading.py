"""Opening offline entries off the GUI thread and owning the sources until they are released."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, TypeVar, cast
from unittest.mock import MagicMock

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.background_jobs import JobContext, LatestRequest
from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer import offline_entry_media
from ax_devil.modules.video_viewer.loading_indicator import LoadingIndicator
from ax_devil.modules.video_viewer.offline_entry_media import (
    EntryMedia,
    entry_opening_scope,
    open_entry_media,
    release_media,
)
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.core import (
    BookmarkTarget,
    EntryLane,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistContent,
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


def _job(token: CancelToken | None = None) -> MagicMock:
    """Return a job context whose token is *token* and whose progress reports poll it, as the real one does."""
    job = MagicMock(spec=JobContext)
    job.token = token or CancelToken()
    job.report.side_effect = lambda _message: job.token.raise_if_cancelled()
    return job


def _open_now(entry: PlaylistEntry) -> EntryMedia:
    return open_entry_media(entry, _all_lanes(entry), QThread.currentThread(), _job())


def _start_opening(owner: QObject, entry: PlaylistEntry, delivered: list[EntryMedia]) -> LatestRequest:
    """Submit resource-bearing media work using the viewer's request and release contracts."""
    request = LatestRequest(owner)
    request.submit(
        entry_opening_scope(owner),
        "open test entry",
        partial(open_entry_media, entry, _all_lanes(entry), QThread.currentThread()),
        on_result=delivered.append,
        on_error=lambda error: pytest.fail(str(error)),
        on_discard=release_media,
    )
    return request


def _wait_released(qtbot: QtBot, sources: list[FileFrameSource], workers: set[threading.Thread]) -> None:
    qtbot.waitUntil(
        lambda: not any(shiboken6.isValid(source) for source in sources) and not any(w.is_alive() for w in workers)
    )


def _threads_started_by(action: Callable[[], _T]) -> tuple[_T, set[threading.Thread]]:
    baseline = set(threading.enumerate())
    result = action()
    return result, set(threading.enumerate()) - baseline


def test_openings_of_different_viewers_share_one_queue_and_a_cancelled_waiting_one_never_starts(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Viewers open one entry at a time in order; an entry abandoned while waiting is never opened at all."""
    release_first = threading.Event()
    events: list[str] = []

    def open_video(video: SeekableVideoContent) -> MagicMock:
        events.append(f"{video.display_name} started")
        if video.display_name == "first":
            assert release_first.wait(5)
        return MagicMock()

    monkeypatch.setattr(offline_entry_media, "create_frame_source", open_video)
    owners = [QObject(), QObject(), QObject()]
    delivered: list[EntryMedia] = []
    openings = [
        _start_opening(owner, _entry(Path(name)), delivered)
        for owner, name in zip(owners, ("first", "abandoned", "third"), strict=True)
    ]
    qtbot.waitUntil(lambda: events == ["first started"])
    openings[1].cancel()
    release_first.set()
    qtbot.waitUntil(lambda: len(delivered) == 2)

    assert events == ["first started", "third started"]
    assert all(media.error is None for media in delivered)


def test_opened_media_lives_on_the_gui_thread_and_shares_one_source_per_video(
    qtbot: QtBot, video_file_factory: Callable[[float, int], Path]
) -> None:
    """Lanes over the same video share its source, and every source is handed to the GUI thread ready for use."""
    path = video_file_factory(0.1, 10)
    delivered: list[EntryMedia] = []
    owner = QObject()

    video = SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=path))
    shared = PlaylistEntry(
        lanes=tuple(EntryLane(display_name=name, video=video, default_considered=True) for name in ("a", "b")),
        default_considered=True,
    )

    _start_opening(owner, shared, delivered)
    qtbot.waitUntil(lambda: len(delivered) == 1)
    media = delivered[0]
    try:
        assert media.error is None
        assert len(media.video_sources) == 1
        assert [lane.source_index for lane in media.lanes] == [0, 0]
        assert media.video_sources[0].thread() is QThread.currentThread()
        assert media.video_sources[0].get_total_frames() == 1
    finally:
        release_media(media)
        _wait_released(qtbot, media.video_sources, set())


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


@pytest.mark.parametrize("end", ["abandon", "viewer destroyed"])
def test_unwanted_opening_opens_nothing_more_and_releases_what_it_opened(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, video_file_factory: Callable[[float, int], Path], end: str
) -> None:
    """Losing interest mid-open, or the viewer going away, stops before the next video and never delivers; the video
    already opening is still released."""
    path = video_file_factory(0.1, 10)
    opening_started = threading.Event()
    finish_opening = threading.Event()
    opened: list[FileFrameSource] = []
    source_threads: set[threading.Thread] = set()
    original_open = offline_entry_media.create_frame_source

    def slow_open(video: SeekableVideoContent) -> FileFrameSource:
        opening_started.set()
        assert finish_opening.wait(5)
        # Only the source's own threads must stop; the opening and release workers live for the whole process.
        source, started = _threads_started_by(lambda: original_open(video))
        source_threads.update(started)
        opened.append(source)
        return source

    monkeypatch.setattr(offline_entry_media, "create_frame_source", slow_open)
    delivered: list[EntryMedia] = []
    owner = QObject()

    opening = _start_opening(owner, _entry(path, video_file_factory(0.2, 10)), delivered)
    qtbot.waitUntil(opening_started.is_set)
    if end == "abandon":
        opening.cancel()
    else:
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finish_opening.set()

    qtbot.waitUntil(lambda: len(opened) == 1)
    _wait_released(qtbot, opened, source_threads)
    assert len(opened) == 1
    assert delivered == []


_STAGES = ("video open", "timing analysis", "overlay open", "history placement", "alignment analysis")


class _StagedOpen:
    """One entry with an overlay, opened over mock sources; *during_stage* runs as each blocking step starts."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, during_stage: Callable[[str], None]) -> None:
        self.source = MagicMock()
        self.overlay_source = MagicMock()
        self.reached: list[str] = []
        self._during_stage = during_stage
        self.source.get_timing_profile.side_effect = lambda: self._run("timing analysis")
        self.overlay_source.scene_history.side_effect = lambda *_args, **_kwargs: self._run("history placement")
        self.overlay_source.analyze_alignment.side_effect = lambda *_args: self._run("alignment analysis")
        policy = MagicMock()
        policy.sample_selection.return_value = (True, None)
        monkeypatch.setattr(
            offline_entry_media, "create_frame_source", lambda _video: self._run("video open", self.source)
        )
        monkeypatch.setattr(
            offline_entry_media,
            "create_overlay_source",
            lambda _overlay, *, frame_timeline: self._run("overlay open", (self.overlay_source, policy)),
        )
        overlay = OverlayContent(
            display_name="overlay",
            source_spec=FileOverlaySourceSpec(path=Path("/tmp/overlay.txt"), handler_type="TEST_FILE"),
        )
        video = SeekableVideoContent(
            display_name="video", source_spec=FileVideoSourceSpec(path=Path("/tmp/video.mp4")), overlays=(overlay,)
        )
        self.entry = PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True)

    def _run(self, stage: str, result: Any = None) -> Any:
        self.reached.append(stage)
        self._during_stage(stage)
        return result

    def expected_reached(self, last_stage: str) -> list[str]:
        return list(_STAGES[: _STAGES.index(last_stage) + 1])

    def assert_released_once(self) -> None:
        self.source.stop.assert_called_once()
        self.source.deleteLater.assert_called_once()
        opened_overlay = int("overlay open" in self.reached)
        assert self.overlay_source.close.call_count == opened_overlay
        assert self.overlay_source.deleteLater.call_count == opened_overlay


@pytest.mark.parametrize("cancel_during", _STAGES[:-1])
def test_cancelled_open_skips_every_later_step_and_returns_what_it_opened(
    monkeypatch: pytest.MonkeyPatch, cancel_during: str
) -> None:
    """Cancelling during any blocking open stops at the next poll; the media opened so far goes back to be released."""
    job = _job()
    opening = _StagedOpen(monkeypatch, lambda stage: job.token.cancel() if stage == cancel_during else None)

    media = open_entry_media(opening.entry, _all_lanes(opening.entry), QThread.currentThread(), job)

    assert opening.reached == opening.expected_reached(cancel_during)
    assert media.error is not None
    assert media.video_sources == [opening.source]
    assert media.overlay_sources() == ([opening.overlay_source] if "overlay open" in opening.reached else [])


def test_opening_cancelled_before_it_starts_opens_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job()
    job.token.cancel()
    opening = _StagedOpen(monkeypatch, lambda _stage: None)

    media = open_entry_media(opening.entry, _all_lanes(opening.entry), QThread.currentThread(), job)

    assert opening.reached == []
    assert media.error is not None
    assert media.video_sources == []


def test_cancelling_between_lanes_returns_the_lane_already_opened_and_opens_no_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    sources = [MagicMock(), MagicMock()]
    first = sources[0]
    first.get_timing_profile.side_effect = job.token.cancel
    monkeypatch.setattr(offline_entry_media, "create_frame_source", lambda _video: sources.pop(0))
    entry = _entry(Path("first"), Path("second"))

    media = open_entry_media(entry, _all_lanes(entry), QThread.currentThread(), job)

    assert media.video_sources == [first]
    assert len(sources) == 1  # The second video was never opened.


@pytest.mark.parametrize("abandon_during", _STAGES)
def test_abandoned_opening_releases_every_opened_source_once_and_delivers_nothing(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, abandon_during: str
) -> None:
    """Losing interest at any step, even the last one that lets the open finish anyway, releases once, delivers none."""
    in_stage = threading.Event()
    resume = threading.Event()

    def block_at_stage(stage: str) -> None:
        if stage == abandon_during:
            in_stage.set()
            assert resume.wait(5)

    opening_steps = _StagedOpen(monkeypatch, block_at_stage)
    delivered: list[EntryMedia] = []
    owner = QObject()

    opening = _start_opening(owner, opening_steps.entry, delivered)
    qtbot.waitUntil(in_stage.is_set)
    opening.cancel()
    resume.set()

    qtbot.waitUntil(lambda: opening_steps.source.deleteLater.called)
    assert opening_steps.reached == opening_steps.expected_reached(abandon_during)[: len(opening_steps.reached)]
    opening_steps.assert_released_once()
    assert delivered == []


@pytest.mark.parametrize("action", ["close", "replace"])
def test_retired_viewer_media_outlives_its_widgets_without_blocking_the_gui(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    video_file_factory: Callable[[float, int], Path],
    render_catalog_manager: SceneRenderCatalogManager,
    action: str,
) -> None:
    """Closing a pane and changing entry both release once in the background, even after the pane is destroyed."""
    path = video_file_factory(0.1, 10)
    playlist = PlaylistContent(display_name="playlist", entries=(_entry(path), _entry(path)))
    widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(widget)
    widget.on_workspace_attached()
    qtbot.waitUntil(lambda: widget._runtime is not None)
    assert widget._runtime is not None
    sources = widget._runtime.iter_video_sources()
    assert len(sources) == 1
    source = sources[0]
    stop_started = threading.Event()
    finish_stop = threading.Event()
    stop_threads: list[threading.Thread] = []
    destroyed_threads: list[QThread] = []
    source.destroyed.connect(lambda: destroyed_threads.append(QThread.currentThread()))
    original_stop = source.stop

    def gated_stop() -> None:
        stop_threads.append(threading.current_thread())
        stop_started.set()
        assert finish_stop.wait(5)
        original_stop()

    monkeypatch.setattr(source, "stop", gated_stop)
    try:
        if action == "replace":
            widget.step_next_entry()
        else:
            widget.cleanup()
        qtbot.waitUntil(stop_started.is_set)
        heartbeat: list[bool] = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qtbot.waitUntil(lambda: bool(heartbeat))
        assert len(stop_threads) == 1
        assert stop_threads[0] is not threading.current_thread()
        assert shiboken6.isValid(source)
        widget.cleanup()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(widget)
        assert shiboken6.isValid(source)
    finally:
        finish_stop.set()
    qtbot.waitUntil(lambda: not shiboken6.isValid(source))
    assert len(stop_threads) == 1
    assert destroyed_threads == [QThread.currentThread()]


def test_viewer_opens_a_real_video_without_waiting_and_stops_its_workers_on_close(
    qtbot: QtBot, video_file_factory: Callable[[float, int], Path], render_catalog_manager: SceneRenderCatalogManager
) -> None:
    """Attaching the viewer returns at once with a loading indicator; closing it leaves no video worker running."""
    path = video_file_factory(0.1, 10)
    video = SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=path))
    widget = OfflineVideoViewerWidget(video, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(widget)
    baseline = set(threading.enumerate())

    widget.on_workspace_attached()

    assert widget.findChildren(FrameDisplay) == []
    assert widget.findChildren(LoadingIndicator)
    qtbot.waitUntil(lambda: len(widget.findChildren(FrameDisplay)) == 1 and not widget.findChildren(LoadingIndicator))
    video_workers = [
        thread for thread in set(threading.enumerate()) - baseline if thread.name.endswith("frame-delivery")
    ]
    assert video_workers

    widget.cleanup()

    qtbot.waitUntil(lambda: not any(worker.is_alive() for worker in video_workers))


def test_a_viewer_opened_at_a_frame_shows_it_paused_and_reports_it_as_the_bookmark_position(
    qtbot: QtBot, video_file_factory: Callable[[float, int], Path], render_catalog_manager: SceneRenderCatalogManager
) -> None:
    """A bookmark opens at its frame, and that frame, not the first, is the first one the viewer shows."""
    video = replace(
        SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=video_file_factory(1.0, 10))),
        item_id="item",
    )
    widget = OfflineVideoViewerWidget(video, render_catalog_manager=render_catalog_manager, start_frame=6)
    qtbot.addWidget(widget)

    widget.on_workspace_attached()
    qtbot.waitUntil(lambda: widget.bookmark_position() is not None)

    position = widget.bookmark_position()
    assert position is not None
    assert (position.target, position.frame) == (BookmarkTarget("item"), 6)
    assert position.time_s == pytest.approx(0.6, abs=0.01)  # The time of the frame on screen: frame 6, never 0.
    assert widget._runtime is not None and not widget._runtime.is_playing

    runtime = widget._runtime
    widget.show_frame(2)  # A bookmark of the open entry seeks it in place, without reopening the video.

    qtbot.waitUntil(lambda: (shown := widget.bookmark_position()) is not None and shown.frame == 2)
    assert widget._runtime is runtime and not runtime.is_playing
    widget.cleanup()


class _RetiringSource(QObject):
    """A decoder whose bounded stop returns while its external read is still blocked."""

    def __init__(self, finish_read: threading.Event | None = None) -> None:
        super().__init__()
        self.stopped = threading.Event()
        self.stop_calls = 0
        self.delete_calls = 0
        self.delete_threads: list[QThread] = []
        self.worker: threading.Thread | None = None
        if finish_read is not None:
            self.worker = threading.Thread(target=finish_read.wait, daemon=True)
            self.worker.start()

    def stop(self) -> None:
        """Model the decoder's bounded stop after its join timeout expires."""
        self.stop_calls += 1
        self.stopped.set()

    def wait(self, timeout: int = 2000) -> bool:
        """Report actual decoder exit, honoring the caller's wait bound."""
        if self.worker is None:
            return True
        self.worker.join(timeout / 1000)
        return not self.worker.is_alive()

    def deleteLater(self) -> None:  # noqa: N802
        """Record disposal before handing it to Qt's GUI deletion queue."""
        self.delete_calls += 1
        self.delete_threads.append(QThread.currentThread())
        super().deleteLater()


def test_a_stuck_decoder_does_not_prevent_other_media_from_stopping_and_being_deleted(qtbot: QtBot) -> None:
    """Retired media remains owned until decoder exit; a blocked read cannot strand later releases."""
    finish_read = threading.Event()
    stuck = _RetiringSource(finish_read)
    same_entry = _RetiringSource()
    independent = _RetiringSource()
    sources = [stuck, same_entry, independent]
    release_media(EntryMedia(video_sources=[cast(FileFrameSource, stuck), cast(FileFrameSource, same_entry)]))
    try:
        qtbot.waitUntil(stuck.stopped.is_set)
        release_media(EntryMedia(video_sources=[cast(FileFrameSource, independent)]))
        qtbot.waitUntil(lambda: not shiboken6.isValid(independent))
        assert independent.stopped.is_set()
        assert same_entry.stopped.is_set()
        assert shiboken6.isValid(stuck)
        assert shiboken6.isValid(same_entry)
        assert stuck.worker is not None and stuck.worker.is_alive()
        assert stuck.delete_calls == 0
        assert same_entry.delete_calls == 0
    finally:
        finish_read.set()
        qtbot.waitUntil(lambda: all(not shiboken6.isValid(source) for source in sources))
    assert all(source.stop_calls == 1 and source.delete_calls == 1 for source in sources)
    assert all(source.delete_threads == [QThread.currentThread()] for source in sources)
