"""Unit tests for VideoFrameReader class.

Tests VideoFrameReader behavior in isolation:
- State management and transitions
- Threading behavior and synchronization
- Resource management and cleanup
- Error handling and edge cases

Note: Integration tests are in test_integration.py
"""

import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Generator

import numpy as np
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_worker import WorkerState
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import (
    FrameReaderWorker,
    VideoFrameReader,
)
from ax_devil.modules.settings.logging_config import get_logger
from tests.helpers.video import create_test_video

logger = get_logger(__name__)


@pytest.fixture(scope="session")
def sample_video_file(test_temp_root: Path) -> Path:
    """Provide a reusable sample video on disk for tests that need an existing file."""
    video_path = test_temp_root / "sample_videoplayback.mp4"
    if not video_path.exists():
        create_test_video(video_path, duration=2.0, fps=30)
    return video_path


def _create_asf_video_with_start_pts(path: Path) -> None:
    """Create an ASF video whose first decoded frame is not returned by jump_to(0)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=3:rate=25:size=320x240",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "60",
        "-keyint_min",
        "60",
        "-f",
        "asf",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


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


def _assert_reader_state(reader: VideoFrameReader, expected: WorkerState) -> None:
    assert reader.state == expected


class TestCoreState:
    """Test core internal state management and transitions."""

    def test_reader_lifecycle(self, reader: VideoFrameReader) -> None:
        """Opening, pausing, resuming and closing keep state and worker lifetime consistent."""
        assert reader.state == WorkerState.STOPPED
        assert reader.is_stopped and not reader.is_running and not reader.is_paused
        reader.open()
        _assert_reader_state(reader, WorkerState.RUNNING)
        assert reader.is_running and not reader.is_stopped and not reader.is_paused
        assert reader._worker is not None
        thread = reader._worker._worker_thread
        assert thread is not None and thread.is_alive()
        reader.pause()
        _assert_reader_state(reader, WorkerState.PAUSED)
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
        reader.open()
        worker = reader._worker
        assert worker is not None
        paused = threading.Event()
        wait_for_resume = worker._resume_event.wait

        def observed_wait(timeout: float | None = None) -> bool:
            paused.set()
            return wait_for_resume(timeout)

        monkeypatch.setattr(worker._resume_event, "wait", observed_wait)
        try:
            reader.pause()
            assert reader.is_paused
            worker.signal_work_available()
            assert paused.wait(timeout=2.0), "Worker did not enter its paused wait"
            reader.close()
            assert worker.wait(timeout_ms=0)
            assert reader.is_stopped
        finally:
            worker._resume_event.set()
            reader.close()
            assert worker.wait(timeout_ms=2000)


class TestInvalidOperations:
    """Test internal operations called in invalid states."""

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
        """
        Test duplicate operations produce appropriate warnings.

        Validates:
        - _start() on already RUNNING reader warns but doesn't create duplicate threads
        - _pause() on already PAUSED reader warns
        - _resume() on RUNNING (not paused) reader warns
        - _stop() on already STOPPED reader warns
        """
        # Test duplicate start
        reader.open()
        assert reader._worker is not None
        original_thread = reader._worker._worker_thread
        reader.open()  # Should warn
        assert reader._worker is not None
        assert reader._worker._worker_thread is original_thread  # Same thread

        # Test duplicate pause
        reader.pause()
        reader.pause()  # Should warn
        assert reader.is_paused

        # Test resume on running
        reader.resume()
        reader.resume()  # Should warn (not paused)
        assert reader.is_running

        # Test duplicate stop
        reader.close()
        reader.close()  # Should warn
        assert reader.is_stopped

    def test_start_on_paused_reader(self, reader: VideoFrameReader) -> None:
        """
        Test starting paused reader produces warning.

        Validates that calling _start() on a PAUSED reader warns
        without changing state (must resume first).
        """
        reader.open()
        reader.pause()
        reader.open()  # Should warn
        assert reader.is_paused  # State unchanged


class TestConcurrency:
    """Test thread safety and concurrent operations."""

    def test_concurrent_starts(self, reader: VideoFrameReader) -> None:
        """
        Test multiple threads starting simultaneously.

        Validates race condition protection in _start() method:
        - Multiple threads call _start() simultaneously
        - Only one should succeed (first to acquire lock)
        - Others should get warnings
        - Only one worker thread should be created
        - Reader should be in RUNNING state after completion

        This test catches race conditions in the critical section
        of the _start() method where _state is checked and updated.
        """
        results = []

        def start_worker() -> None:
            try:
                reader.open()
                results.append("success")
            except Exception as e:
                results.append(f"error: {e}")

        # Launch 5 threads simultaneously
        threads = [threading.Thread(target=start_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have one success, others warned
        assert reader.is_running
        assert reader._worker is not None
        assert reader._worker._worker_thread is not None
        assert reader._worker._worker_thread.is_alive()
        # No exceptions should occur
        assert all("error" not in result for result in results)

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

    def test_no_thread_leaks(self, sample_video_file: Path) -> None:
        """Closing each reader joins its worker, independently of unrelated threads."""
        for i in range(10):
            reader = VideoFrameReader(str(sample_video_file))
            try:
                reader.open()
                worker = reader._worker
                assert worker is not None
                if i % 3 == 1:
                    reader.pause()
                    reader.resume()
            finally:
                reader.close()

            assert worker.wait(timeout_ms=0), f"Reader {i} left its worker running after close()"


class TestExceptionHandling:
    """Test worker thread exception handling and recovery."""

    def test_reader_can_restart_after_close(self, sample_video_file: Path) -> None:
        """Reopening restores frame delivery through both synchronous and asynchronous APIs."""
        reader = VideoFrameReader(str(sample_video_file))
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
        finally:
            reader.close()
        assert reader.is_stopped


class TestPublicAPI:
    """Test the new public API methods."""

    def test_close_method(self, reader: VideoFrameReader) -> None:
        """
        Test close() method stops worker and cleans up resources.

        Validates:
        - close() shuts down ThreadPoolExecutor
        - Worker thread is stopped
        - State transitions to STOPPED
        """
        reader.open()
        assert reader.is_running

        reader.close()
        assert reader.is_stopped
        # ThreadPoolExecutor should be shut down
        assert reader._callback_executor._shutdown

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

    def test_multiple_get_frame_asyncs(self, reader: VideoFrameReader) -> None:
        """
        Test multiple get_frame_async() calls work concurrently.

        Validates:
        - Multiple frame requests are handled
        - All callbacks are executed
        - ThreadPoolExecutor handles concurrent callbacks
        """
        reader.open()

        callback_count = 0
        callback_lock = threading.Lock()
        all_callbacks_done = threading.Event()

        def count_callback(frame_data: Any) -> None:
            nonlocal callback_count
            with callback_lock:
                callback_count += 1
                if callback_count == 3:
                    all_callbacks_done.set()

        # Request multiple frames
        reader.get_frame_async(20, count_callback)
        reader.get_frame_async(21, count_callback)
        reader.get_frame_async(22, count_callback)

        # Wait for all callbacks
        assert all_callbacks_done.wait(timeout=3.0), "Not all callbacks completed"
        assert callback_count == 3

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

    def test_get_frame_sync_cache_miss(self, reader: VideoFrameReader) -> None:
        """An uncached request returns and retains the correctly decoded frame."""
        reader.open()
        worker = reader._frame_reader_worker
        assert worker is not None and not worker._cache.contains(25)
        frame = reader.get_frame_sync(25)
        assert frame is not None and frame.frame_index == 25
        assert frame.pixels.shape == (240, 320, 3)
        assert frame.pixels.dtype == np.uint8
        assert worker._cache.contains(25)

    def test_get_frame_sync_timeout(self, reader: VideoFrameReader) -> None:
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

    def test_get_frame_sync_after_async(self, reader: VideoFrameReader) -> None:
        """
        Test get_frame_sync() works correctly after async requests.

        Validates:
        - get_frame_sync() can be used after get_frame_async()
        - Mixed async/sync usage works correctly
        - Cache state is consistent between methods
        """
        reader.open()

        # Make async request first
        async_callback_called = threading.Event()
        async_result = []

        def async_callback(frame_data: Any) -> None:
            async_result.append(frame_data)
            async_callback_called.set()

        reader.get_frame_async(20, async_callback)
        assert async_callback_called.wait(timeout=2.0), "Async callback not called"

        # Now make sync request for same frame (should hit cache)
        sync_frame = reader.get_frame_sync(20)

        # Both should return the same data
        assert sync_frame is not None
        assert async_result[0] is not None
        assert isinstance(sync_frame, DecodedFrame)
        assert isinstance(async_result[0], DecodedFrame)
        assert np.array_equal(sync_frame.pixels, async_result[0].pixels)
        assert sync_frame.timestamp_us == async_result[0].timestamp_us

        # Make sync request for new frame
        sync_frame_new = reader.get_frame_sync(25)
        assert sync_frame_new is not None
        assert isinstance(sync_frame_new, DecodedFrame)


class TestPrefetchBehavior:
    """Test the new prefetching functionality."""

    def test_open_does_not_prefetch_before_first_frame_request(
        self, reader: VideoFrameReader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opening reaches an idle worker without decoding or retaining frames."""
        idle = threading.Event()
        do_work = FrameReaderWorker.do_work

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

    def test_first_explicit_request_reads_asf_start_frame_without_eager_prefetch(self, tmp_path: Path) -> None:
        """Regression: ASF frame 0 must be readable even when open() did not prefetch it."""
        video_path = tmp_path / "start.asf"
        _create_asf_video_with_start_pts(video_path)
        reader = VideoFrameReader(str(video_path))
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
        do_work = FrameReaderWorker.do_work

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

        Simulates the rare timeout case by using a stub PyAV abstraction whose read_until() raises
        when asked to read "backwards". Without the fix this would time out because the worker
        swallows the exception and never fulfills the request.
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

            def to_ndarray(self, format: str = "rgb24") -> np.ndarray:
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
            assert stub.jump_calls, "Expected jump_to() fallback when read_until() cannot rewind"
        finally:
            reader.close()
