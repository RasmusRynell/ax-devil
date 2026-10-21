"""Opening offline entries off the GUI thread and owning the sources until they are released."""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, TypeVar
from unittest.mock import MagicMock

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.background_jobs import JobContext, LatestRequest
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.video_viewer import offline_entry_media
from ax_devil.modules.video_viewer.offline_entry_media import (
    EntryMedia,
    entry_opening_scope,
    open_entry_media,
    release_media,
)
from ax_devil.modules.workspace.core import (
    EntryLane,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from tests.helpers.offline_viewer import playlist_entry

_T = TypeVar("_T")


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
        _start_opening(owner, playlist_entry(Path(name)), delivered)
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
    entry = playlist_entry(path, tmp_path / "missing.mp4")

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

    opening = _start_opening(owner, playlist_entry(path, video_file_factory(0.2, 10)), delivered)
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
            lambda _overlay, *, frame_timeline: self._run(
                "overlay open", offline_entry_media.LaneOverlay(self.overlay_source, policy)
            ),
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
    entry = playlist_entry(Path("first"), Path("second"))

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
