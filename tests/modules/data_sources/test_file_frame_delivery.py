"""Tests for serialized file-frame delivery behavior."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from PySide6.QtCore import Qt

import ax_devil.modules.data_sources.file_frame_delivery as delivery_module
from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import FrameReaderWorker
from ax_devil.modules.data_sources.file_frame_delivery import FileFrameDelivery
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from tests.helpers.video import create_test_video


class _Decoder:
    """Decoded-frame test double used to isolate delivery scheduling."""

    def __init__(self, total_frames: int = 10) -> None:
        self.read_frames: list[int] = []
        self.prefetch_attempts = 0
        self.prefetched = threading.Event()
        self.cleaned_up = False
        self._total_frames = total_frames

    def read_decoded_frame(self, frame_number: int) -> DecodedFrame:
        """Return one deterministic decoded frame."""
        self.read_frames.append(frame_number)
        return DecodedFrame(
            frame_index=frame_number,
            pixels=np.zeros((2, 2, 3), dtype=np.uint8),
            timestamp_us=float(frame_number * 1_000),
        )

    def prefetch_one(self) -> bool:
        """Report one bounded prefetch step."""
        self.prefetch_attempts += 1
        self.prefetched.set()
        return False

    def get_total_frames(self) -> int:
        """Return the fixed source length."""
        return self._total_frames

    def get_frame_period_after_s(self, _frame_number: int) -> None:
        """Use the delivery FPS fallback cadence."""
        return None

    def cleanup(self) -> None:
        """Record worker-owned cleanup."""
        self.cleaned_up = True


def _make_delivery(
    frames: list[tuple[int, int]],
    finished: threading.Event,
) -> tuple[FileFrameDelivery, _Decoder]:
    decoder = _Decoder()
    delivery = FileFrameDelivery(
        decoder=cast(FrameReaderWorker, decoder),
        fps=30.0,
        source_id="test-source",
        on_playback_frame=lambda frame, generation: frames.append((frame.frame_index, generation)),
        on_playback_finished=finished.set,
    )
    delivery.open()
    return delivery, decoder


def test_delivery_handles_seek_and_non_playback_reads_on_its_one_worker() -> None:
    """A seek and a non-playback read share one serialized decoder path."""
    frames: list[tuple[int, int]] = []
    finished = threading.Event()
    delivery, decoder = _make_delivery(frames, finished)
    async_result: list[int | None] = []
    async_done = threading.Event()

    def on_async_frame(frame: DecodedFrame | None) -> None:
        """Record the non-playback result."""
        async_result.append(frame.frame_index if frame is not None else None)
        async_done.set()

    try:
        delivery.jump_to(4)
        delivery.request_frame_async(7, on_async_frame)

        assert async_done.wait(timeout=1.0)
        assert frames == [(4, 1)]
        assert async_result == [7]
        assert decoder.read_frames == [4, 7]
    finally:
        delivery.close()

    assert delivery.wait()
    assert decoder.cleaned_up


def test_delivery_prefetches_between_paced_playback_frames() -> None:
    """Lazy prefetch runs after playback starts and before its next frame deadline."""
    frames: list[tuple[int, int]] = []
    finished = threading.Event()
    delivery, decoder = _make_delivery(frames, finished)

    try:
        assert delivery.play()
        assert decoder.prefetched.wait(timeout=1.0)
    finally:
        delivery.close()

    assert frames[0] == (0, 0)


def test_delivery_plays_every_frame_then_reports_the_end() -> None:
    """Playback on the real worker delivers each frame once, in order, then signals the end."""
    frames: list[tuple[int, int]] = []
    finished = threading.Event()
    delivery = FileFrameDelivery(
        decoder=cast(FrameReaderWorker, _Decoder(total_frames=3)),
        fps=100.0,
        source_id="eof",
        on_playback_frame=lambda frame, generation: frames.append((frame.frame_index, generation)),
        on_playback_finished=finished.set,
    )
    delivery.open()
    try:
        assert delivery.play()
        assert finished.wait(timeout=1.0)
    finally:
        delivery.close()

    assert frames == [(0, 0), (1, 0), (2, 0)]


def test_paused_delivery_holds_its_frame_until_played_again() -> None:
    """Pausing the real worker stops paced frames; playing again continues to the end."""
    frames: list[int] = []
    first_frame = threading.Event()
    finished = threading.Event()
    delivery: FileFrameDelivery

    def on_playback_frame(frame: DecodedFrame, _generation: int) -> None:
        """Record playback and pause on the first frame, before the worker picks the next one."""
        frames.append(frame.frame_index)
        if frame.frame_index == 0:
            delivery.pause()
            first_frame.set()

    delivery = FileFrameDelivery(
        decoder=cast(FrameReaderWorker, _Decoder(total_frames=3)),
        fps=100.0,
        source_id="pause",
        on_playback_frame=on_playback_frame,
        on_playback_finished=finished.set,
    )
    delivery.open()
    try:
        assert delivery.play()
        assert first_frame.wait(timeout=1.0)
        assert not finished.wait(timeout=0.1), "Paused playback must not run on to the end"
        assert frames == [0]

        assert delivery.play()
        assert finished.wait(timeout=1.0)
    finally:
        delivery.close()

    assert frames == [0, 1, 2]


def _paced_frames(
    monkeypatch: pytest.MonkeyPatch, total_frames: int, times: list[float], *, pause_after: int | None = None
) -> list[int | None]:
    """Return the playback frame chosen at each clock reading at 8 fps, or None when playback finished.

    With *pause_after*, playback is paused and resumed after that many readings.
    """
    clock = iter(times)
    monkeypatch.setattr(delivery_module, "time", SimpleNamespace(perf_counter=lambda: next(clock)))
    delivery = FileFrameDelivery(
        decoder=cast(FrameReaderWorker, _Decoder(total_frames=total_frames)),
        fps=8.0,
        source_id="pacing",
        on_playback_frame=lambda _frame, _generation: None,
        on_playback_finished=lambda: None,
    )
    frames: list[int | None] = []
    for reading in range(len(times)):
        if reading == pause_after:
            delivery.pause()
            assert delivery.play()
        request, finished, wait_seconds = delivery._next_playback_request()
        assert wait_seconds is None, "Every clock reading in these tests is at or past a deadline"
        if finished:
            frames.append(None)
        else:
            assert request is not None
            frames.append(request.frame_number)
    return frames


def test_on_time_playback_delivers_every_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """Playback that keeps up delivers consecutive frames."""
    assert _paced_frames(monkeypatch, 10, [0.0, 0.125, 0.25, 0.375]) == [0, 1, 2, 3]


def test_late_playback_skips_to_the_frame_due_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Late playback follows the clock instead of slowing down."""
    assert _paced_frames(monkeypatch, 10, [0.0, 0.45, 0.5]) == [0, 3, 4]


def test_late_playback_still_delivers_the_last_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipping stops at the last frame, and the next step reports the end of playback."""
    assert _paced_frames(monkeypatch, 5, [0.0, 10.0, 10.0]) == [0, 4, None]


def test_resumed_playback_continues_from_the_held_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """Time spent paused is not treated as lateness, so resuming does not skip frames."""
    assert _paced_frames(monkeypatch, 10, [0.0, 10.0, 10.125], pause_after=1) == [0, 1, 2]


def test_file_frame_source_uses_one_delivery_worker_for_playback_and_seek(
    qtbot: object,
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concrete source has one delivery worker and preserves exact seek delivery."""
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: temp_dir / "cache")
    video_path = temp_dir / "delivery.mp4"
    create_test_video(video_path, duration=1.0, fps=20)
    video_reader_workers_before = sum(thread.name == "VideoFrameReader" for thread in threading.enumerate())
    source = FileFrameSource(str(video_path), source_id="delivery-test")
    received_frame_numbers: list[int] = []
    source.frameReady.connect(
        lambda frame: received_frame_numbers.append(frame.frame_id.sequence_id),
        Qt.ConnectionType.QueuedConnection,
    )

    try:
        assert source.play()
        qtbot.waitUntil(lambda: len(received_frame_numbers) >= 2)  # type: ignore[attr-defined]
        thread_names = [thread.name for thread in threading.enumerate()]
        assert thread_names.count("delivery-test-frame-delivery") == 1
        assert thread_names.count("VideoFrameReader") == video_reader_workers_before

        source.pause()
        source.jump_to(7)
        qtbot.waitUntil(lambda: received_frame_numbers[-1] == 7)  # type: ignore[attr-defined]
    finally:
        source.stop()

    assert source.wait()


def test_file_frame_source_reports_its_length_without_reading_every_timestamp(
    temp_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The length is the last frame's time plus its own period; it needs no full timestamp list."""
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: temp_dir / "cache")
    video_path = temp_dir / "length.mp4"
    create_test_video(video_path, duration=1.0, fps=20)
    source = FileFrameSource(str(video_path), source_id="length-test")
    try:
        duration = source.get_duration_s()
        assert duration == pytest.approx(source.get_total_frames() / 20, abs=0.001)
        assert source.peek_frame_seconds(10) == pytest.approx(0.5, abs=0.001)
        assert source.peek_frame_seconds(source.get_total_frames()) is None
    finally:
        source.stop()
    assert source.wait()
    assert source.get_duration_s() is None
