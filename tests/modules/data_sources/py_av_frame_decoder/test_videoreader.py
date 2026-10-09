"""Frame delivery, ffmpeg pixel accuracy, caching and reader lifecycle."""

import random
import subprocess
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import av
import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_worker import WorkerState
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import (
    FrameReaderWorker,
    VideoFrameReader,
)
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


@pytest.fixture
def reader(request: Any, video_file_factory: Callable[[float, int], Path]) -> Generator[VideoFrameReader, None, None]:
    """Provide a fresh VideoFrameReader instance for each test.

    Can be parametrized with video settings:
    @pytest.mark.parametrize('reader', [{'duration': 2.0, 'fps': 30}], indirect=True)
    """
    param = getattr(request, "param", {})
    duration = param.get("duration", 1.0)
    fps = param.get("fps", 30)

    video_path = video_file_factory(duration, fps)
    reader_instance = VideoFrameReader(str(video_path))
    yield reader_instance
    reader_instance.close()


def _reader_threads() -> set[threading.Thread]:
    return {thread for thread in threading.enumerate() if thread.name == "VideoFrameReader"}


class TestCoreState:
    """Test observable state transitions and worker lifetime."""

    def test_reader_lifecycle(self, reader: VideoFrameReader) -> None:
        """Opening, pausing, resuming and closing keep state and worker lifetime consistent."""
        assert reader.state == WorkerState.STOPPED
        assert reader.is_stopped and not reader.is_running and not reader.is_paused
        before = _reader_threads()
        reader.open()
        assert reader.is_running and not reader.is_stopped and not reader.is_paused
        started = _reader_threads() - before
        assert len(started) == 1
        thread = started.pop()
        reader.pause()
        assert reader.is_paused and not reader.is_running and not reader.is_stopped
        assert thread.is_alive()
        reader.resume()
        assert reader.is_running and not reader.is_paused and not reader.is_stopped
        assert reader.get_frame_sync(0) is not None
        reader.close()
        assert reader.state == WorkerState.STOPPED
        assert reader.is_stopped and not reader.is_running and not reader.is_paused
        assert not thread.is_alive()

    def test_stop_while_paused(self, reader: VideoFrameReader, monkeypatch: pytest.MonkeyPatch) -> None:
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
            assert reader.is_paused
            assert paused.wait(timeout=2.0), "Worker did not enter its paused wait"
            reader.close()
            assert _reader_threads() == before
            assert reader.is_stopped
        finally:
            for event in resume_events:
                event.set()
            reader.close()


class TestInvalidOperations:
    """Test unsupported lifecycle transitions and idempotent operations."""

    def test_operations_on_stopped_reader(self, reader: VideoFrameReader) -> None:
        """
        Test operations on stopped reader raise RuntimeError.

        Validates that calling pause(), resume() on a STOPPED
        reader raises RuntimeError with appropriate messages.

        close() on stopped reader should be safe (idempotent).
        """
        # These should raise RuntimeError since reader is not open
        with pytest.raises(RuntimeError, match="VideoFrameReader is not open"):
            reader.pause()

        with pytest.raises(RuntimeError, match="VideoFrameReader is not open"):
            reader.resume()

        # close() should be safe on stopped reader
        reader.close()  # Should not raise

        # State should remain STOPPED
        assert reader.is_stopped

    def test_duplicate_operations(self, reader: VideoFrameReader) -> None:
        """Repeated lifecycle operations preserve state and do not create extra workers."""
        # Test duplicate start
        before = _reader_threads()
        reader.open()
        opened = _reader_threads()
        assert len(opened - before) == 1
        reader.open()
        assert _reader_threads() == opened

        # Test duplicate pause
        reader.pause()
        reader.pause()
        assert reader.is_paused

        # Test resume on running
        reader.resume()
        reader.resume()
        assert reader.is_running

        # Test duplicate stop
        reader.close()
        reader.close()
        assert reader.is_stopped

    def test_start_on_paused_reader(self, reader: VideoFrameReader) -> None:
        """Opening an already paused reader preserves its paused state."""
        reader.open()
        reader.pause()
        reader.open()
        assert reader.is_paused  # State unchanged


class TestConcurrency:
    """Test thread safety and concurrent operations."""

    def test_concurrent_state_changes(self, reader: VideoFrameReader) -> None:
        """
        Test rapid state changes from multiple threads.

        Validates lock correctness under high contention:
        - Multiple threads rapidly call _pause()/_resume()
        - Another thread continuously reads state properties
        - No exceptions should occur
        - Reader should remain functional after stress test
        - Final state should be consistent

        This test validates that _state_lock properly protects
        all state modifications and reads.
        """
        reader.open()
        errors = []

        def state_changer(operation: str) -> None:
            try:
                for i in range(50):  # Increased iterations
                    if operation == "pause":
                        reader.pause()
                    elif operation == "resume":
                        reader.resume()
                    elif operation == "check":
                        # Rapid property access
                        _ = reader.state
                        _ = reader.is_running
                        _ = reader.is_paused
                        _ = reader.is_stopped
                    time.sleep(0.001)  # Minimal delay
            except Exception as e:
                errors.append(f"{operation}: {e}")

        # Create contending threads
        threads = [
            threading.Thread(target=state_changer, args=("pause",)),
            threading.Thread(target=state_changer, args=("resume",)),
            threading.Thread(target=state_changer, args=("check",)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No exceptions should occur
        assert len(errors) == 0, f"Concurrency errors: {errors}"

        # Reader should still be functional
        reader.close()
        assert reader.is_stopped


class TestResourceManagement:
    """Test proper resource cleanup and memory management."""

    def test_no_thread_leaks(self, video_file_factory: Callable[[float, int], Path]) -> None:
        """Closing each reader joins its worker, independently of unrelated threads."""
        before = _reader_threads()
        for i in range(3):
            reader = VideoFrameReader(str(video_file_factory(1.0, 30)))
            try:
                reader.open()
                assert len(_reader_threads() - before) == 1
                if i % 3 == 1:
                    reader.pause()
                    reader.resume()
            finally:
                reader.close()

            assert _reader_threads() == before, f"Reader {i} left its worker running after close()"


class TestExceptionHandling:
    """Test worker thread exception handling and recovery."""

    def test_reader_can_restart_after_close(self, video_file_factory: Callable[[float, int], Path]) -> None:
        """Reopening restores both delivery APIs, and closing joins callback threads."""
        before = set(threading.enumerate())
        reader = VideoFrameReader(str(video_file_factory(1.0, 30)))
        try:
            reader.open()
            assert reader.get_frame_sync(0) is not None
            reader.close()
            assert reader.is_stopped
            reader.open()
            assert reader.is_running
            frame = reader.get_frame_sync(0)
            assert frame is not None and frame.frame_index == 0
            received: list[DecodedFrame | None] = []
            completed = threading.Event()

            def callback(frame: DecodedFrame | None) -> None:
                received.append(frame)
                completed.set()

            reader.get_frame_async(1, callback)
            assert completed.wait(2.0), "Reopened reader did not deliver its async callback"
            assert len(received) == 1
            async_frame = received[0]
            assert async_frame is not None and async_frame.frame_index == 1
            callbacks = {thread for thread in threading.enumerate() if thread.name.startswith("callback_")} - before
            assert callbacks
        finally:
            reader.close()
        assert reader.is_stopped
        assert all(not thread.is_alive() for thread in callbacks)


class TestPublicAPI:
    """Test public frame requests, callback isolation and cache reuse."""

    def test_get_frame_async_callback(self, reader: VideoFrameReader) -> None:
        """
        Test get_frame_async() method executes callback asynchronously.

        Validates:
        - get_frame_async() queues request
        - Callback is executed with frame data
        - Callback runs in ThreadPoolExecutor
        - Frame is cached after request
        """
        reader.open()

        callback_result = []
        callback_called = threading.Event()

        def test_callback(frame_data: Any) -> None:
            callback_result.append(frame_data)
            callback_called.set()

        # Request a specific frame
        reader.get_frame_async(10, test_callback)

        # Wait for callback to be called
        assert callback_called.wait(timeout=2.0), "Callback was not called within timeout"

        # Verify callback received correct data
        assert len(callback_result) == 1

        frame_data = callback_result[0]
        assert isinstance(frame_data, DecodedFrame)
        assert frame_data.frame_index == 10
        assert frame_data.timestamp_us >= 0.0
        assert frame_data.pixels.shape == (240, 320, 3)  # Height, Width, Channels
        assert frame_data.pixels.dtype == np.uint8

        cached_frame = reader.get_frame_sync(10)
        assert cached_frame is not None and cached_frame.frame_index == frame_data.frame_index
        np.testing.assert_array_equal(cached_frame.pixels, frame_data.pixels)

    def test_callback_exception_handling(self, reader: VideoFrameReader) -> None:
        """
        Test that callback exceptions don't crash the system.

        Validates:
        - Callback exceptions are caught and logged
        - Other callbacks still work after one fails
        - System remains stable
        """
        reader.open()

        success_callback_called = threading.Event()
        failing_callback_called = threading.Event()

        def failing_callback(frame_data: Any) -> None:
            assert frame_data is not None
            failing_callback_called.set()
            raise RuntimeError("Test callback error")

        def success_callback(frame_data: Any) -> None:
            assert frame_data is not None
            success_callback_called.set()

        # Request frame with failing callback
        reader.get_frame_async(28, failing_callback)
        assert failing_callback_called.wait(2.0), "Failing callback was not called"

        # Request frame with successful callback
        reader.get_frame_async(29, success_callback)

        # Success callback should still work
        assert success_callback_called.wait(timeout=2.0), "Success callback failed after error"
        assert reader.is_running  # System should still be running

    def test_get_frame_sync_cache_hit(self, reader: VideoFrameReader, monkeypatch: pytest.MonkeyPatch) -> None:
        """A repeated request returns the cached frame without invoking the decoder."""
        reader.open()
        first = reader.get_frame_sync(5)
        assert first is not None and first.frame_index == 5
        reader.pause()

        def unexpected_decode(worker: FrameReaderWorker, frame_index: int) -> None:
            raise AssertionError(f"Cached frame {frame_index} required decoding")

        monkeypatch.setattr(FrameReaderWorker, "_decode_and_cache_frame", unexpected_decode)
        # Resume request handling with background prefetch disabled for the cache-hit assertion.
        monkeypatch.setattr(FrameReaderWorker, "_do_prefetch_step", lambda worker: False)
        reader.resume()
        cached = reader.get_frame_sync(5)
        assert cached is not None and cached.frame_index == first.frame_index
        assert cached.timestamp_us == first.timestamp_us
        np.testing.assert_array_equal(cached.pixels, first.pixels)

    def test_get_frame_sync_on_unopened_reader(self, reader: VideoFrameReader) -> None:
        """
        Test get_frame_sync() raises RuntimeError when reader is not open.

        Validates:
        - get_frame_sync() raises RuntimeError immediately when reader is not open
        - Error message is descriptive
        """
        # Don't start the reader - should raise immediately
        with pytest.raises(RuntimeError, match="VideoFrameReader is not open"):
            reader.get_frame_sync(10, timeout=0.5)

    def test_get_frame_async_on_unopened_reader(self, reader: VideoFrameReader) -> None:
        """
        Test get_frame_async() raises RuntimeError when reader is not open.

        Validates:
        - get_frame_async() raises RuntimeError immediately when reader is not open
        - Error message is descriptive
        """

        def dummy_callback(frame_data: Any) -> None:
            pass

        # Don't start the reader - should raise immediately
        with pytest.raises(RuntimeError, match="VideoFrameReader is not open"):
            reader.get_frame_async(10, dummy_callback)

    def test_get_frame_sync_multiple_concurrent(self, reader: VideoFrameReader) -> None:
        """
        Test multiple concurrent get_frame_sync() calls work correctly.

        Validates:
        - Multiple threads can call get_frame_sync simultaneously
        - All requests are fulfilled correctly
        - No deadlocks or race conditions
        """
        reader.open()

        results = []
        errors = []
        completed_count = threading.Semaphore(0)

        def sync_worker(frame_index: int) -> None:
            try:
                frame_data = reader.get_frame_sync(frame_index, timeout=3.0)
                results.append((frame_index, frame_data))
                completed_count.release()
            except Exception as e:
                errors.append(f"Frame {frame_index}: {e}")
                completed_count.release()

        # Launch 5 concurrent requests (use valid frame indices 0-29)
        frame_indices = [24, 25, 26, 27, 28]
        threads = [threading.Thread(target=sync_worker, args=(idx,)) for idx in frame_indices]

        for t in threads:
            t.start()

        # Wait for all to complete
        for _ in range(5):
            assert completed_count.acquire(timeout=5.0), "Not all sync requests completed"

        for t in threads:
            t.join()

        # Verify results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 5

        # All should have valid frame data
        for frame_index, frame_data in results:
            assert frame_data is not None, f"Frame {frame_index} returned None"
            assert isinstance(frame_data, DecodedFrame)
            assert frame_data.frame_index == frame_index


class TestPrefetchBehavior:
    """Test the new prefetching functionality."""

    def test_open_does_not_prefetch_before_first_frame_request(
        self, reader: VideoFrameReader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opening reaches an idle worker without decoding or retaining frames."""
        idle = threading.Event()
        do_work: Callable[[FrameReaderWorker], bool] = FrameReaderWorker.do_work

        def observe_idle(worker: FrameReaderWorker) -> bool:
            worked = do_work(worker)
            if not worked:
                idle.set()
            return worked

        monkeypatch.setattr(FrameReaderWorker, "do_work", observe_idle)
        reader.open()
        assert idle.wait(2.0), "Worker did not become idle"
        reader.pause()
        worker = reader._frame_reader_worker
        assert worker is not None
        assert worker._cache.size() == 0
        assert reader._frame_processor.current_frame_index == 0

    def test_first_explicit_request_reads_asf_start_frame_without_eager_prefetch(self, asf_video: Path) -> None:
        """Regression: ASF frame 0 must be readable even when open() did not prefetch it."""
        reader = VideoFrameReader(str(asf_video))
        try:
            reader.open()

            frame = reader.get_frame_sync(0, timeout=2.0)

            assert frame is not None
            assert frame.frame_index == 0
            assert frame.timestamp_us == 0.0
        finally:
            reader.close()

    @pytest.mark.parametrize("reader", [{"duration": 2.0, "fps": 30}], indirect=True)
    @pytest.mark.parametrize("requests", [(15,), (5,), (50, 10), (58,)], ids=["forward", "boundary", "backward", "end"])
    def test_prefetched_frames_are_cached_without_further_decoding(
        self, reader: VideoFrameReader, monkeypatch: pytest.MonkeyPatch, requests: tuple[int, ...]
    ) -> None:
        """Async requests populate the forward window and stop at its boundary or end of video."""
        idle = threading.Event()
        target = requests[-1]
        do_work: Callable[[FrameReaderWorker], bool] = FrameReaderWorker.do_work

        def observe_idle(worker: FrameReaderWorker) -> bool:
            worked = do_work(worker)
            if not worked and worker._prefetch_started and worker._current_read_frame == target:
                idle.set()
            return worked

        monkeypatch.setattr(FrameReaderWorker, "do_work", observe_idle)
        reader.open()
        for requested in requests:
            completed = threading.Event()
            received: list[DecodedFrame | None] = []

            def callback(frame: DecodedFrame | None) -> None:
                received.append(frame)
                completed.set()

            reader.get_frame_async(requested, callback)
            assert completed.wait(2.0), f"Frame {requested} callback did not complete"
            assert len(received) == 1
            frame = received[0]
            assert frame is not None and frame.frame_index == requested
        assert idle.wait(2.0), "Prefetch did not finish"
        reader.pause()
        worker = reader._frame_reader_worker
        assert worker is not None
        boundary = min(target + worker._prefetch_count, reader.get_total_frames())
        assert all(worker._cache.contains(index) for index in range(target, boundary))
        assert not worker._cache.contains(boundary), "Prefetch exceeded its forward window"

        def unexpected_decode(worker: FrameReaderWorker, frame_index: int) -> None:
            raise AssertionError(f"Cached frame {frame_index} required decoding")

        monkeypatch.setattr(FrameReaderWorker, "_decode_and_cache_frame", unexpected_decode)
        monkeypatch.setattr(FrameReaderWorker, "_do_prefetch_step", lambda worker: False)
        reader.resume()
        for index in range(target, boundary):
            cached = reader.get_frame_sync(index)
            assert cached is not None and cached.frame_index == index

    def test_get_frame_sync_backward_seek_regression(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Regression: ensure backward seeks still resolve frames after prefetch moved far ahead.

        Uses a stub PyAV abstraction whose read_until() raises when asked to read "backwards": a frame behind the
        decoder must be reached with jump_to() instead of timing out.
        """

        class _DummyFrame:
            """Decoded frame stub with no source duration metadata."""

            duration = 0
            time_base = None
            width = 2
            height = 2
            planes: tuple[()] = ()

            def __init__(self, index: int) -> None:
                self.index = index

            def to_ndarray(self, format: str = "rgb24") -> NDArray[Any]:
                return np.zeros((2, 2, 3), dtype=np.uint8)

        class _StubPyAv:
            def __init__(self, video_path: str, cache_path: Path | None, **kwargs: object) -> None:
                self._current_frame_index = 0
                self._total_frames = 200
                self.jump_calls: list[tuple[int, int]] = []

            @property
            def current_frame_index(self) -> int:
                return self._current_frame_index

            def get_total_frames(self) -> int:
                return self._total_frames

            def get_nearest_keyframe_before(self, frame_index: int) -> int:
                return frame_index

            def get_frame_time_us(self, frame_index: int) -> float:
                return frame_index * 33_333.0

            def get_frame_period_after_s(self, frame_index: int) -> float:
                return 1.0 / 30.0

            def get_frame_source_timing_metadata(self, frame_index: int) -> dict[str, object]:
                return {"video_pts": frame_index, "video_timestamp_source": "stub"}

            def read_next(self) -> _DummyFrame:
                frame = _DummyFrame(self._current_frame_index)
                self._current_frame_index += 1
                return frame

            def read_until(self, target_frame_index: int) -> list[tuple[int, _DummyFrame]]:
                if self._current_frame_index > target_frame_index:
                    raise ValueError("Cannot read backwards in stub")

                decoded: list[tuple[int, _DummyFrame]] = []
                while self._current_frame_index <= target_frame_index:
                    decoded.append((self._current_frame_index, _DummyFrame(self._current_frame_index)))
                    self._current_frame_index += 1
                return decoded

            def jump_to(self, frame_index: int) -> list[tuple[int, _DummyFrame]]:
                self.jump_calls.append((frame_index, self._current_frame_index))
                self._current_frame_index = frame_index + 1
                return [(frame_index, _DummyFrame(frame_index))]

            def close(self) -> None:
                pass

        monkeypatch.setattr(
            "ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader.PyAvAbstraction",
            _StubPyAv,
        )

        reader = VideoFrameReader("dummy-path.mp4", cache_budget_bytes=5 * 12, prefetch_count=10)
        try:
            reader.open()
            reader.pause()

            worker = reader._frame_reader_worker
            assert worker is not None
            stub = worker._frame_processor
            assert isinstance(stub, _StubPyAv)

            worker._cache.clear()
            worker._current_read_frame = 60
            stub._current_frame_index = 90

            reader.resume()
            frame = reader.get_frame_sync(61, timeout=0.3)

            assert frame is not None, "Backward seek should return a frame instead of timing out"
            assert isinstance(frame, DecodedFrame)
            assert stub.jump_calls, "Expected jump_to() for a frame behind the decoder"
        finally:
            reader.close()


class TestVideoFrameReaderIntegration:
    """Test complete VideoFrameReader integration with real video files."""

    @pytest.fixture
    def test_video(self, video_file_factory: Callable[[float, int], Path]) -> Path:
        """Create a test video for integration testing."""
        return video_file_factory(3.0, 30)

    def test_concurrent_access_patterns(self, test_video: Path) -> None:
        """Test concurrent access from multiple threads."""
        reader = VideoFrameReader(str(test_video), cache_budget_bytes=100 * 320 * 240 * 3, callback_threads=4)

        try:
            reader.open()

            # Concurrent frame requests
            frame_results = {}
            timed_out: list[int] = []
            request_lock = threading.Lock()

            def get_frame_asyncs(thread_id: int, frame_range: range) -> None:
                """Request frames from a specific thread."""
                thread_results = []

                for frame_idx in frame_range:
                    received = []
                    completed = threading.Event()

                    def callback(frame_data: DecodedFrame | None, idx: int = frame_idx) -> None:
                        received.append((idx, frame_data))
                        completed.set()

                    reader.get_frame_async(frame_idx, callback)

                    if not completed.wait(2.0):
                        with request_lock:
                            timed_out.append(frame_idx)
                        return
                    thread_results.append(received[0])

                with request_lock:
                    frame_results[thread_id] = thread_results

            # Launch multiple threads requesting different frame ranges
            threads = []
            for i in range(3):
                start_frame = i * 10
                end_frame = start_frame + 5
                thread = threading.Thread(target=get_frame_asyncs, args=(i, range(start_frame, end_frame)))
                threads.append(thread)
                thread.start()

            # Wait for all threads
            for thread in threads:
                thread.join(timeout=5.0)

            assert not timed_out, f"Requests timed out: {timed_out}"
            assert all(not thread.is_alive() for thread in threads)

            # Verify results - require exact expected frame count
            total_received = sum(len(results) for results in frame_results.values())
            expected_total = 3 * 5  # 3 threads × 5 frames each
            assert total_received == expected_total, f"Must receive all {expected_total} frames, got {total_received}"

            # Verify frame data quality
            for thread_id, results in frame_results.items():
                for frame_idx, frame_data in results:
                    assert frame_data is not None
                    assert isinstance(frame_data, DecodedFrame)
                    assert frame_data.frame_index == frame_idx
                    assert frame_data.pixels.shape[2] == 3  # RGB24

        finally:
            reader.close()

    def test_error_conditions_and_recovery(self, temp_dir: Path, test_video: Path) -> None:
        """Invalid media is rejected; an invalid request completes and leaves valid reads usable."""
        invalid_video = temp_dir / "invalid.mp4"
        invalid_video.write_text("This is not a video file")
        with pytest.raises(av.error.InvalidDataError):
            VideoFrameReader(str(invalid_video)).open()

        reader = VideoFrameReader(str(test_video))
        try:
            reader.open()
            received: list[DecodedFrame | None] = []
            completed = threading.Event()

            def callback(frame: DecodedFrame | None) -> None:
                received.append(frame)
                completed.set()

            reader.get_frame_async(reader.get_total_frames(), callback)
            assert completed.wait(2.0), "Invalid request did not complete"
            assert received == [None]
            recovered = reader.get_frame_sync(0)
            assert recovered is not None and recovered.frame_index == 0
            assert np.var(recovered.pixels) > 0
        finally:
            reader.close()


@pytest.fixture(scope="module")
def video_and_references(
    video_file_factory: Callable[[float, int], Path], tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, Path]:
    """Share immutable media and reference PNGs; each case owns its reader and cache."""
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
    """Mixed sync/async requests retain exact indices and independent reference pixels."""
    video, references = video_and_references
    reader = VideoFrameReader(str(video), cache_budget_bytes=30 * 320 * 240 * 3, prefetch_count=10)
    try:
        reader.open()
        for request_number, index in enumerate(indices):
            if request_number % 2 == 0:
                decoded = reader.get_frame_sync(index, timeout=2.0)
            else:
                received: list[DecodedFrame | None] = []
                completed = threading.Event()

                def callback(frame: DecodedFrame | None) -> None:
                    received.append(frame)
                    completed.set()

                reader.get_frame_async(index, callback)
                assert completed.wait(2.0), f"Frame {index} callback did not complete"
                assert len(received) == 1
                decoded = received[0]
            assert decoded is not None, f"Failed to decode frame {index}"
            assert decoded.frame_index == index
            reference = cv2.imread(str(references / f"frame_{index + 1:04d}.png"))
            assert reference is not None, f"Failed to load reference frame {index}"
            np.testing.assert_array_equal(decoded.pixels, cv2.cvtColor(reference, cv2.COLOR_BGR2RGB))
    finally:
        reader.close()
