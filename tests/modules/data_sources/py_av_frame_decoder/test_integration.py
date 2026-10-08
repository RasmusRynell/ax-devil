"""Integration tests for the video frame reader stack.

Tests the whole system working together:
- VideoFrameReader + PyAvAbstraction + FrameIndex
- Real video file processing end-to-end
- Performance characteristics and threading behavior
- No mocking - real functionality only
"""

import threading
from collections.abc import Callable
from pathlib import Path

import av
import numpy as np
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import PyAvAbstraction
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import VideoFrameReader


class TestVideoFrameReaderIntegration:
    """Test complete VideoFrameReader integration with real video files."""

    @pytest.fixture
    def test_video(self, video_file_factory: Callable[[float, int], Path]) -> Path:
        """Create a test video for integration testing."""
        return video_file_factory(3.0, 30)

    def test_complete_video_reader_workflow(self, test_video: Path) -> None:
        """Test complete VideoFrameReader workflow from open to close."""
        reader = VideoFrameReader(
            str(test_video),
            cache_budget_bytes=50 * 320 * 240 * 3,
            prefetch_count=20,
            callback_threads=2,
            worker_timeout=3.0,
        )

        try:
            # Initial state should be stopped
            assert reader.is_stopped
            assert not reader.is_running
            assert not reader.is_paused

            # Open the reader
            reader.open()
            assert reader.is_running

            # Test synchronous frame access
            frame = reader.get_frame_sync(0, timeout=2.0)
            assert frame is not None  # Should successfully retrieve frame
            assert isinstance(frame, DecodedFrame)
            assert frame.frame_index == 0

            # Test asynchronous frame request
            received_frames: list[DecodedFrame | None] = []
            callbacks_done = threading.Event()
            callbacks_lock = threading.Lock()

            def frame_callback(frame_data: DecodedFrame | None) -> None:
                with callbacks_lock:
                    received_frames.append(frame_data)
                    if len(received_frames) == 3:
                        callbacks_done.set()

            # Request multiple frames
            for i in [5, 10, 15]:
                reader.get_frame_async(i, frame_callback)

            # Wait for callbacks
            assert callbacks_done.wait(timeout=2.0), "Not all frame callbacks completed"

            # Should have received all frames
            assert len(received_frames) == 3
            for frame_data in received_frames:
                assert frame_data is not None
                assert isinstance(frame_data, DecodedFrame)
                assert frame_data.pixels.dtype == np.uint8
                assert len(frame_data.pixels.shape) == 3
                assert frame_data.pixels.shape[2] == 3  # RGB24
                assert frame_data.timestamp_us >= 0.0

                # Validate frame contains actual image data
                assert not np.all(frame_data.pixels == 0), "Frame is all black - decode failure"
                assert np.var(frame_data.pixels) > 0, "Frame has no variance - corrupted data"

            # Now synchronous access should work for previously requested frames
            cached_frame = reader.get_frame_sync(5, timeout=1.0)
            assert cached_frame is not None
            assert isinstance(cached_frame, DecodedFrame)

            # Continue with a nearby frame after mixing synchronous and asynchronous requests
            nearby_frame = reader.get_frame_sync(6, timeout=2.0)
            assert nearby_frame is not None

        finally:
            reader.close()
            assert reader.is_stopped

    @pytest.mark.parametrize(
        "indices", [(0, 1, 2, 3, 4, 5), (0, 22, 45, 67), (10, 50, 20, 80, 30)], ids=["sequential", "jumps", "random"]
    )
    def test_every_seek_delivers_the_requested_frame(self, test_video: Path, indices: tuple[int, ...]) -> None:
        """Every request in sequential, jumping and backward patterns delivers correct pixels."""
        reference = PyAvAbstraction(str(test_video))
        expected = []
        try:
            while (frame := reference.read_next()) is not None:
                expected.append(frame.to_ndarray(format="rgb24"))
        finally:
            reference.close()
        reader = VideoFrameReader(str(test_video), prefetch_count=10)
        try:
            reader.open()
            for index in indices:
                received: list[DecodedFrame | None] = []
                completed = threading.Event()

                def callback(frame: DecodedFrame | None) -> None:
                    received.append(frame)
                    completed.set()

                reader.get_frame_async(index, callback)
                assert completed.wait(2.0), f"Frame {index} callback did not complete"
                assert len(received) == 1
                decoded = received[0]
                assert decoded is not None and decoded.frame_index == index
                np.testing.assert_array_equal(decoded.pixels, expected[index])
        finally:
            reader.close()

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
