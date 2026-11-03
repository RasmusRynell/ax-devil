"""Offline viewers hand their opened media on and stop its workers when they close."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer
from pytestqt.qtbot import QtBot

from ax_devil.core.time_units import ms_to_seconds
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.loading_indicator import LoadingIndicator
from ax_devil.modules.video_viewer.offline_entry_media import (
    EntryMedia,
    release_media,
)
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.core import (
    BookmarkTarget,
    FileVideoSourceSpec,
    PlaylistContent,
    SeekableVideoContent,
)
from tests.helpers.offline_viewer import playlist_entry


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
    playlist = PlaylistContent(display_name="playlist", entries=(playlist_entry(path), playlist_entry(path)))
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
    """Attaching the viewer returns at once with a loading indicator; closing it leaves no worker or open view."""
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
    assert len(render_catalog_manager.catalogs_in_use.paths()) == 1

    widget.cleanup()

    qtbot.waitUntil(lambda: not any(worker.is_alive() for worker in video_workers))
    assert render_catalog_manager.catalogs_in_use.paths() == ()


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
    assert widget._runtime is not None and not widget._runtime.playback.is_playing

    runtime = widget._runtime
    widget.show_frame(2)  # A bookmark of the open entry seeks it in place, without reopening the video.

    qtbot.waitUntil(lambda: (shown := widget.bookmark_position()) is not None and shown.frame == 2)
    assert widget._runtime is runtime and not runtime.playback.is_playing
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

    def wait(self, timeout_ms: int = 2000) -> bool:
        """Report actual decoder exit, honoring the caller's wait bound."""
        if self.worker is None:
            return True
        self.worker.join(ms_to_seconds(timeout_ms))
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
