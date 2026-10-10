"""Frame delivery, ffmpeg pixel accuracy and reader lifecycle."""

import random
import subprocess
import threading
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import (
    FrameReaderWorker,
    VideoFrameReader,
)
from tests.helpers.video import drain_prefetch


@pytest.fixture
def reader(video_file_factory: Callable[[float, int], Path]) -> Generator[VideoFrameReader, None, None]:
    """Provide a fresh reader over a one-second, 30 fps video."""
    reader_instance = VideoFrameReader(str(video_file_factory(1.0, 30)))
    yield reader_instance
    reader_instance.close()


def _reader_threads() -> set[threading.Thread]:
    return {thread for thread in threading.enumerate() if thread.name == "VideoFrameReader"}


def _request_async(reader: VideoFrameReader, index: int) -> DecodedFrame | None:
    """Request one frame asynchronously and return what its callback received."""
    received: list[DecodedFrame | None] = []
    completed = threading.Event()

    def callback(frame: DecodedFrame | None) -> None:
        received.append(frame)
        completed.set()

    reader.get_frame_async(index, callback)
    assert completed.wait(2.0), f"Frame {index} callback did not complete"
    assert len(received) == 1
    return received[0]


def test_reader_lifecycle(reader: VideoFrameReader) -> None:
    """Requests need an open reader; repeated transitions keep one worker, and close joins it."""
    operations: tuple[Callable[[], object], ...] = (
        reader.pause,
        reader.resume,
        lambda: reader.get_frame_sync(0),
        lambda: _request_async(reader, 0),
    )
    for operation in operations:
        with pytest.raises(RuntimeError, match="VideoFrameReader is not open"):
            operation()
    reader.close()  # Closing an unopened reader is safe.
    assert reader.is_stopped

    before = _reader_threads()
    reader.open()
    reader.open()
    started = _reader_threads() - before
    assert len(started) == 1
    thread = started.pop()
    assert reader.is_running

    reader.pause()
    reader.pause()
    reader.open()  # Opening a paused reader keeps it paused.
    assert reader.is_paused and thread.is_alive()
    reader.resume()
    reader.resume()
    assert reader.is_running
    frame = reader.get_frame_sync(0)
    assert frame is not None and frame.frame_index == 0

    reader.close()
    reader.close()
    assert reader.is_stopped
    assert not thread.is_alive()
    assert _reader_threads() == before


def test_stop_while_paused(reader: VideoFrameReader, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing a paused reader wakes and joins its worker before returning."""
    paused = threading.Event()
    resume_events: list[threading.Event] = []
    event_wait = threading.Event.wait

    def observed_wait(event: threading.Event, timeout: float | None = None) -> bool:
        if threading.current_thread().name == "VideoFrameReader" and timeout is None:
            resume_events.append(event)
            paused.set()
        return event_wait(event, timeout)

    monkeypatch.setattr(threading.Event, "wait", observed_wait)
    before = _reader_threads()
    reader.open()
    try:
        reader.pause()
        assert paused.wait(timeout=2.0), "Worker did not enter its paused wait"
        reader.close()
        assert _reader_threads() == before
        assert reader.is_stopped
    finally:
        for event in resume_events:
            event.set()
        reader.close()


def test_reader_can_restart_after_close(reader: VideoFrameReader) -> None:
    """Reopening restores both delivery APIs, and closing joins callback threads."""
    before = set(threading.enumerate())
    reader.open()
    assert reader.get_frame_sync(0) is not None
    reader.close()
    assert reader.is_stopped
    reader.open()
    frame = reader.get_frame_sync(0)
    assert frame is not None and frame.frame_index == 0
    async_frame = _request_async(reader, 1)
    assert async_frame is not None and async_frame.frame_index == 1
    callbacks = {thread for thread in threading.enumerate() if thread.name.startswith("callback_")} - before
    assert callbacks
    reader.close()
    assert all(not thread.is_alive() for thread in callbacks)


def test_failing_callback_does_not_stop_later_deliveries(reader: VideoFrameReader) -> None:
    """A callback that raises is isolated from the worker and from later requests."""
    reader.open()
    failing_called = threading.Event()

    def failing_callback(frame: DecodedFrame | None) -> None:
        failing_called.set()
        raise RuntimeError("Test callback error")

    reader.get_frame_async(28, failing_callback)
    assert failing_called.wait(2.0), "Failing callback was not called"
    later = _request_async(reader, 29)
    assert later is not None and later.frame_index == 29
    assert reader.is_running


def test_concurrent_sync_requests_each_receive_their_frame(reader: VideoFrameReader) -> None:
    """Blocking requests from several threads complete with their own frames."""
    reader.open()
    indices = [24, 3, 26, 10, 28]
    with ThreadPoolExecutor(max_workers=len(indices)) as executor:
        frames = list(executor.map(lambda index: reader.get_frame_sync(index, timeout=3.0), indices))
    assert [frame.frame_index if frame is not None else None for frame in frames] == indices


def test_out_of_range_request_completes_and_reader_recovers(reader: VideoFrameReader) -> None:
    """An invalid index completes with no frame and leaves valid reads usable."""
    reader.open()
    assert _request_async(reader, reader.get_total_frames()) is None
    recovered = reader.get_frame_sync(0)
    assert recovered is not None and recovered.frame_index == 0
    assert np.var(recovered.pixels) > 0


def test_first_read_of_asf_returns_its_start_frame(asf_video: Path) -> None:
    """Regression: ASF frame 0 must be readable as the first request, at time zero."""
    worker = FrameReaderWorker(str(asf_video), 1024**2, prefetch_count=10)
    try:
        frame = worker.read_decoded_frame(0)
        assert frame is not None
        assert frame.frame_index == 0
        assert frame.timestamp_us == 0.0
    finally:
        worker.cleanup()


@pytest.fixture(scope="module")
def video_and_references(
    video_file_factory: Callable[[float, int], Path], tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, Path]:
    """Share immutable media and reference PNGs; each case owns its decoder and cache."""
    video = video_file_factory(2.0, 30)
    references = tmp_path_factory.mktemp("reference-frames")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vf", "format=rgb24", str(references / "frame_%04d.png")],
        capture_output=True,
        check=True,
    )
    assert len(list(references.glob("frame_*.png"))) == 60
    return video, references


@pytest.mark.parametrize(
    "indices",
    [tuple(range(20)), (0, 10, 25, 5, 35, 15), (0, 59, 1, 58), tuple(random.Random(7).sample(range(60), 16))],
    ids=["sequential", "forward-backward-jumps", "first-last-frames", "seeded-random"],
)
def test_frames_match_ffmpeg(video_and_references: tuple[Path, Path], indices: tuple[int, ...]) -> None:
    """Decoded, cached and prefetched frames keep exact indices and independent reference pixels."""
    video, references = video_and_references
    worker = FrameReaderWorker(str(video), 30 * 320 * 240 * 3, prefetch_count=10)
    try:
        for index in indices:
            decoded = worker.read_decoded_frame(index)
            assert decoded is not None, f"Failed to decode frame {index}"
            assert decoded.frame_index == index
            reference = cv2.imread(str(references / f"frame_{index + 1:04d}.png"))
            assert reference is not None, f"Failed to load reference frame {index}"
            np.testing.assert_array_equal(decoded.pixels, cv2.cvtColor(reference, cv2.COLOR_BGR2RGB))
            # Fill the prefetch window between reads, as playback does, so later reads hit prefetched frames.
            drain_prefetch(worker, limit=20)
    finally:
        worker.cleanup()
