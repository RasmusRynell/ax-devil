"""Preparing the next playlist entry in a child process leaves caches that opening the entry loads as they are."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QThread

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.background_jobs import JobContext
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_index import FrameIndex
from ax_devil.modules.data_sources.file_data_provider.stores import IndexedFrameSceneStore, SourceIndexedSceneStore
from ax_devil.modules.video_viewer import entry_preparation as preparation_module
from ax_devil.modules.video_viewer.entry_preparation import (
    EntryPreparation,
    entry_preparation,
    overlay_specs,
    prepare_entry,
)
from ax_devil.modules.video_viewer.offline_entry_media import open_entry_media
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.workspace.core import (
    EntryLane,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.plugins.decoders.mot.plugin import MOT_FILE
from tests.helpers.video import create_test_video


def test_entry_opens_from_caches_prepared_by_the_child_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp: object
) -> None:
    """The prepared entry opens without building its frame index or overlay data again."""
    video_path = tmp_path / "next.mp4"
    create_test_video(video_path, duration=1.0, fps=10)
    overlay_path = tmp_path / "next.csv"
    overlay_path.write_text("1,1,0,0,64,48,0.9,1,0.8\n2,2,10,12,40,50,0.5,3,0.4\n", encoding="utf-8")
    video = SeekableVideoContent(display_name="next", source_spec=FileVideoSourceSpec(path=video_path))
    overlay = OverlayContent(
        display_name="mot",
        source_spec=FileOverlaySourceSpec(path=overlay_path, handler_type=MOT_FILE, decoder_kwargs={"width": 64}),
    )
    entry = PlaylistEntry(
        lanes=(EntryLane(display_name="next", video=video, default_considered=True, overlay=overlay),),
        default_considered=True,
    )

    request = entry_preparation(entry, (0,), ())
    assert request is not None
    started: list[subprocess.Popen[bytes]] = []
    start = subprocess.Popen

    def record_start(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        started.append(start(*args, **kwargs))
        return started[-1]

    monkeypatch.setattr(subprocess, "Popen", record_start)
    prepare_entry(request)
    monkeypatch.undo()
    assert started[0].wait(timeout=120) == 0

    def rebuild(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("rebuilt a cache that preparation should have written")

    monkeypatch.setattr(FrameIndex, "build", rebuild)
    monkeypatch.setattr(IndexedFrameSceneStore, "_rebuild", rebuild)
    monkeypatch.setattr(SourceIndexedSceneStore, "_rebuild", rebuild)
    job = MagicMock(spec=JobContext)
    job.token = CancelToken()
    sticky = OverlayPersistenceSettings.default_enabled()
    media = open_entry_media(entry, (0,), QThread.currentThread(), job, sticky=sticky)
    try:
        assert media.error is None
        assert len(media.overlay_sources()) == 1
    finally:
        media.close()
        media.delete_later()


def _entry_with_overlay(video_path: Path, overlay_path: Path, width: int) -> PlaylistEntry:
    video = SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=video_path))
    overlay = OverlayContent(
        display_name="mot",
        source_spec=FileOverlaySourceSpec(path=overlay_path, handler_type=MOT_FILE, decoder_kwargs={"width": width}),
    )
    lane = EntryLane(display_name="video", video=video, default_considered=True, overlay=overlay)
    return PlaylistEntry(lanes=(lane,), default_considered=True)


def test_preparation_leaves_out_an_overlay_the_playing_entry_reads_with_other_decoder_options(tmp_path: Path) -> None:
    """An overlay cache is named after its file, so rebuilding it for other options would change the playing boxes."""
    annotations = tmp_path / "shared.csv"
    playing = overlay_specs(_entry_with_overlay(tmp_path / "a.mp4", annotations, 64), (0,))

    other_options = entry_preparation(_entry_with_overlay(tmp_path / "b.mp4", annotations, 128), (0,), playing)
    same_options = entry_preparation(_entry_with_overlay(tmp_path / "b.mp4", annotations, 64), (0,), playing)

    assert other_options is not None and other_options.overlays == ()
    assert same_options is not None and len(same_options.overlays) == 1


_HEARTBEAT = """
import sys, time
while True:
    with open(sys.argv[1], "a") as beat:
        beat.write(".")
    time.sleep(0.05)
"""

_STALLED_CHILD = """
import subprocess, sys, time
from pathlib import Path
from ax_devil.core.atomic_file import write_atomically

heartbeat, folder = sys.argv[1], Path(sys.argv[2])
subprocess.Popen([sys.executable, "-I", heartbeat, str(folder / "beat")])
with write_atomically(folder / "frames" / "video.ptsidx", "wb") as file:
    file.write(b"partial")
    file.flush()
    (folder / "ready").write_text("1")
    time.sleep(600)
"""


def _wait_for(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + 20
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.02)


def test_cancelling_preparation_kills_its_descendants_and_removes_its_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled preparation leaves no running process and no half-written cache file, wherever it was stopped."""
    heartbeat = tmp_path / "heartbeat.py"
    heartbeat.write_text(_HEARTBEAT, encoding="utf-8")
    child = tmp_path / "stalled_child.py"
    child.write_text(_STALLED_CHILD, encoding="utf-8")
    cache_dir = tmp_path / "caches"
    command = [sys.executable, "-I", str(child), str(heartbeat), str(cache_dir)]
    monkeypatch.setattr(preparation_module, "_CHILD_COMMAND", command)
    storage = {"cache_dir": str(cache_dir)}

    cancel = prepare_entry(EntryPreparation(videos=(), overlays=(), config_path=tmp_path, storage=storage))
    beat = cache_dir / "beat"
    try:
        _wait_for((cache_dir / "ready").exists, "the child to stall inside a cache write")
        _wait_for(lambda: beat.exists() and beat.stat().st_size > 0, "the child's descendant to run")
        (leftover,) = (cache_dir / "frames").glob("*.tmp")
    finally:
        cancel()

    _wait_for(lambda: not leftover.exists(), "the temporary file to be deleted")
    size = beat.stat().st_size
    time.sleep(0.5)
    assert beat.stat().st_size == size, "a descendant of the cancelled preparation is still running"
