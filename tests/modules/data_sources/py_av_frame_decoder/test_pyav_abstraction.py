"""Tests for pyav_abstraction.py module - real functionality testing only."""

from collections.abc import Callable, Generator
from pathlib import Path

import av
import numpy as np
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import PyAvAbstraction
from tests.helpers.video import create_test_video


class TestPyAvAbstractionInitialization:
    """Test PyAvAbstraction initialization and setup."""

    @pytest.fixture
    def video_path(self, video_file_factory: Callable[[float, int], Path]) -> Path:
        """Create a test video file."""
        video_path = video_file_factory(2.0, 30)
        return video_path

    def test_initialization(self, video_path: Path) -> None:
        """Test basic initialization."""
        reader = PyAvAbstraction(str(video_path))

        # Should create frame index
        assert reader.frame_index is not None

        # Should have valid frame count
        assert reader.get_total_frames() > 0

        reader.close()

    def test_current_frame_index_property(self, video_path: Path) -> None:
        """Test current frame index property."""
        reader = PyAvAbstraction(str(video_path))

        # Property should return valid value
        assert isinstance(reader.current_frame_index, int)
        assert reader.current_frame_index == 0  # starts at 0

        reader.close()


class TestFrameReading:
    """Test frame reading functionality."""

    @pytest.fixture
    def reader(self, video_file_factory: Callable[[float, int], Path]) -> Generator[PyAvAbstraction, None, None]:
        """Create a PyAvAbstraction instance with test video."""
        video_path = video_file_factory(1.0, 30)
        reader = PyAvAbstraction(str(video_path))
        yield reader
        reader.close()

    def test_read_next_frame(self, reader: PyAvAbstraction) -> None:
        """Test reading next frame."""
        video_frame = reader.read_next()

        assert video_frame is not None
        assert isinstance(video_frame, av.VideoFrame)

        # Convert to numpy array for testing
        frame = video_frame.to_ndarray(format="rgb24")
        assert frame is not None
        assert isinstance(frame, np.ndarray)
        assert frame.dtype == np.uint8
        assert len(frame.shape) == 3  # Height, Width, Channels
        assert frame.shape[2] == 3  # RGB24 format

        # Validate frame contains reasonable pixel data (not all zeros or all max)
        assert not np.all(frame == 0), "Frame contains only black pixels - likely decode error"
        assert not np.all(frame == 255), "Frame contains only white pixels - likely decode error"
        assert np.var(frame) > 0, "Frame has no variance - likely corrupted data"

        # Current frame index should be updated
        assert reader.current_frame_index == 1

    def test_read_multiple_frames(self, reader: PyAvAbstraction) -> None:
        """Test reading multiple frames sequentially."""
        video_frames = []
        numpy_frames = []
        for i in range(5):
            video_frame = reader.read_next()
            assert video_frame is not None
            assert isinstance(video_frame, av.VideoFrame)
            video_frames.append(video_frame)

            # Convert to numpy for testing
            numpy_frame = video_frame.to_ndarray(format="rgb24")
            assert numpy_frame is not None
            numpy_frames.append(numpy_frame)
            assert reader.current_frame_index == i + 1

        # All frames should have same dimensions
        for frame in numpy_frames:
            assert frame.shape == numpy_frames[0].shape

        # Sequential frames should have different content (testsrc generates changing patterns)
        for i in range(1, len(numpy_frames)):
            assert not np.array_equal(numpy_frames[i - 1], numpy_frames[i]), (
                f"Frame {i - 1} and {i} are identical - content validation failed"
            )

    def test_read_until_end_of_stream(self, reader: PyAvAbstraction) -> None:
        """Test reading frames until end of stream."""
        frame_count = 0
        total_frames = reader.get_total_frames()

        while True:
            frame = reader.read_next()
            if frame is None:
                break
            frame_count += 1

            # Prevent infinite loop in case of test failure
            if frame_count > total_frames + 10:
                break

        # Should read exactly the expected number of frames - no tolerance for differences
        assert frame_count == total_frames, f"Frame count mismatch: read {frame_count}, expected {total_frames}"
        assert reader.current_frame_index == frame_count


class TestSeeking:
    """Test seeking functionality."""

    @pytest.fixture
    def reader(self, video_file_factory: Callable[[float, int], Path]) -> Generator[PyAvAbstraction, None, None]:
        """Create a PyAvAbstraction instance with longer test video."""
        video_path = video_file_factory(3.0, 30)
        reader = PyAvAbstraction(str(video_path))
        yield reader
        reader.close()

    def test_jump_to_start(self, reader: PyAvAbstraction) -> None:
        """Test jumping to start of video."""
        # Read some frames first
        for _ in range(10):
            reader.read_next()

        # Jump back to start
        decoded_frames = reader.jump_to(0)

        # Verify we got frame 0 in the returned data
        assert len(decoded_frames) > 0
        frame_indices = [idx for idx, _ in decoded_frames]
        assert 0 in frame_indices, "Frame 0 should be in decoded frames"

        # After jump_to(0), we're positioned to read the next frame
        # If we decoded more than just frame 0, current_frame_index will be higher
        assert reader.current_frame_index >= 1

    def test_jump_to_out_of_bounds(self, reader: PyAvAbstraction) -> None:
        """Test jumping to out-of-bounds frame indices raises ValueError."""
        total_frames = reader.get_total_frames()
        original_index = reader.current_frame_index

        # Test negative index raises ValueError
        with pytest.raises(ValueError, match="out of bounds"):
            reader.jump_to(-1)
        assert reader.current_frame_index == original_index  # Should not change

        # Test index beyond video raises ValueError
        with pytest.raises(ValueError, match="out of bounds"):
            reader.jump_to(total_frames + 100)
        assert reader.current_frame_index == original_index  # Should not change

    def test_multiple_seeks(self, reader: PyAvAbstraction) -> None:
        """Forward/backward seeks return the target and leave sequential reading at the next frame."""
        total_frames = reader.get_total_frames()
        positions = [0, total_frames // 4, total_frames // 2, total_frames - 20, total_frames // 4, 0]
        for position in positions:
            decoded = reader.jump_to(position)
            assert position in [index for index, _frame in decoded]
            cursor = reader.current_frame_index
            assert cursor > position
            assert reader.read_next() is not None
            assert reader.current_frame_index == cursor + 1

    def test_seek_and_read_sequence(self, reader: PyAvAbstraction) -> None:
        """Test seeking and then reading multiple frames."""
        total_frames = reader.get_total_frames()
        seek_pos = total_frames // 3

        decoded_frames = reader.jump_to(seek_pos)

        # Verify we got the target frame in the decoded data
        assert len(decoded_frames) > 0
        frame_indices = [idx for idx, _ in decoded_frames]
        assert seek_pos in frame_indices, f"Target frame {seek_pos} should be in decoded frames"

        # Read several frames from current position
        previous_frame = None
        start_position = reader.current_frame_index
        for i in range(5):
            frame = reader.read_next()
            if frame is None:
                break  # Reached end of video
            assert reader.current_frame_index == start_position + i + 1

            # Verify frame content changes (testsrc generates unique patterns)
            frame_array = frame.to_ndarray(format="rgb24")
            if previous_frame is not None:
                assert not np.array_equal(previous_frame, frame_array), (
                    f"Consecutive frames {seek_pos + i} and {seek_pos + i + 1} are identical"
                )
            previous_frame = frame_array

    def test_seek_to_exact_last_frame(self, reader: PyAvAbstraction) -> None:
        """Test seeking to the exact last frame of the video."""
        total_frames = reader.get_total_frames()
        last_frame_index = total_frames - 1

        # Seek to exactly the last frame - this returns the frame data
        decoded_frames = reader.jump_to(last_frame_index)

        # Verify we got the last frame in the returned data
        assert len(decoded_frames) > 0
        frame_indices = [idx for idx, _ in decoded_frames]
        assert last_frame_index in frame_indices, f"Last frame {last_frame_index} should be in decoded frames"
        assert all(frame is not None for _, frame in decoded_frames), "All frames should be valid"

        # After jump_to, current_frame_index points to the next frame to decode
        assert reader.current_frame_index == total_frames

        # Try to read next frame (should be None since we're past the last frame)
        frame_after_end = reader.read_next()
        assert frame_after_end is None, "Expected None when reading past last frame"

    def test_seek_near_end_is_reliable(self, reader: PyAvAbstraction) -> None:
        """Test that seeking near (but not exactly at) end of file works reliably."""
        total_frames = reader.get_total_frames()

        # Test different offsets from the end
        for offset in [5, 10, 20]:
            if total_frames > offset:
                near_end_index = total_frames - offset

                # Jump to frame near end - this returns the frame data
                decoded_frames = reader.jump_to(near_end_index)

                # Verify we got the target frame in the returned data
                assert len(decoded_frames) > 0
                frame_indices = [idx for idx, _ in decoded_frames]
                assert near_end_index in frame_indices, f"Target frame {near_end_index} should be in decoded frames"

                # Current frame index should be positioned for next read
                # (could be higher than target if we decoded additional frames)
                assert reader.current_frame_index > near_end_index

                # Should be able to read next frame if not at end
                if reader.current_frame_index < total_frames:
                    frame = reader.read_next()
                    assert frame is not None, f"Failed to read next frame after seeking to {near_end_index}"


class TestFrameCountConsistency:
    """Test that frame counts are consistent across all components."""

    @pytest.fixture
    def reader(self, video_file_factory: Callable[[float, int], Path]) -> Generator[PyAvAbstraction, None, None]:
        """Create a PyAvAbstraction instance for frame count testing."""
        video_path = video_file_factory(2.0, 30)
        reader = PyAvAbstraction(str(video_path))
        yield reader
        reader.close()

    def test_read_all_frames_exact_count_match(self, reader: PyAvAbstraction) -> None:
        """Test reading ALL frames sequentially and verify count exactly matches metadata."""
        # Get metadata frame count
        metadata_total = reader.get_total_frames()
        frame_index_total = reader.frame_index.get_total_frames()

        # Verify metadata consistency first
        assert metadata_total == frame_index_total, (
            f"Metadata mismatch: analyzer={metadata_total}, frame_index={frame_index_total}"
        )

        # Read all frames sequentially from start
        # jump_to(0) now returns decoded frames, so we need to count them
        decoded_frames = reader.jump_to(0)

        # Validate and count frames from jump_to(0)
        valid_frames_from_jump = 0
        for frame_idx, video_frame in decoded_frames:
            assert video_frame is not None, f"Frame {frame_idx} from jump_to(0) is None"
            assert isinstance(video_frame, av.VideoFrame), f"Frame {frame_idx} from jump_to(0) is not VideoFrame"

            # Convert to numpy for validation
            frame_data = video_frame.to_ndarray(format="rgb24")
            assert frame_data is not None, f"Frame {frame_idx} failed to convert to numpy"
            assert isinstance(frame_data, np.ndarray), f"Frame {frame_idx} converted data is not numpy array"
            assert frame_data.shape[2] == 3, f"Frame {frame_idx} from jump_to(0) not RGB24"
            assert frame_data.dtype == np.uint8, f"Frame {frame_idx} from jump_to(0) wrong dtype"
            valid_frames_from_jump += 1

        # Read remaining frames with read_next()
        frames_read = 0
        valid_frames_from_read_next = 0

        while True:
            next_frame = reader.read_next()
            frames_read += 1

            if next_frame is None:
                break

            valid_frames_from_read_next += 1

            # Convert to numpy for validation
            frame = next_frame.to_ndarray(format="rgb24")
            assert frame is not None, f"Frame {valid_frames_from_read_next} failed to convert to numpy"

            # Verify frame properties
            assert isinstance(frame, np.ndarray), (
                f"Frame {valid_frames_from_read_next} from read_next is not numpy array"
            )
            assert frame.shape[2] == 3, f"Frame {valid_frames_from_read_next} from read_next not RGB24"
            assert frame.dtype == np.uint8, f"Frame {valid_frames_from_read_next} from read_next wrong dtype"

            # Prevent infinite loop
            if frames_read > metadata_total + 10:
                assert False, f"Read too many frames: {frames_read} > {metadata_total + 10}"

        # Calculate total valid frames
        total_valid_frames = valid_frames_from_jump + valid_frames_from_read_next

        # EXACT count verification - no tolerance for differences
        assert total_valid_frames == metadata_total, (
            f"Frame count mismatch! Read {valid_frames_from_jump} from jump + "
            f"{valid_frames_from_read_next} from read_next = {total_valid_frames}, "
            f"metadata says {metadata_total}"
        )

        # Verify final position
        assert reader.current_frame_index == metadata_total, (
            f"Final position mismatch: {reader.current_frame_index} != {metadata_total}"
        )

    def test_sequential_vs_random_access_consistency(self, reader: PyAvAbstraction) -> None:
        """Test that sequential reading and random access yield same total count."""
        total_frames = reader.get_total_frames()

        # Test 1: Sequential read count (includes frames from jump_to(0))
        decoded_frames = reader.jump_to(0)

        # Count valid frames from jump_to(0)
        frames_from_jump = 0
        for frame_idx, frame_data in decoded_frames:
            if frame_data is not None:
                frames_from_jump += 1

        # Count frames from read_next()
        frames_from_read_next = 0
        while True:
            frame = reader.read_next()
            if frame is None:
                break
            frames_from_read_next += 1

        sequential_count = frames_from_jump + frames_from_read_next

        # Test 2: Random access verification - sample every 10th frame
        accessible_frames = 0
        step = max(1, total_frames // 10)  # Sample at most 10 positions

        for i in range(0, total_frames, step):
            decoded_frames = reader.jump_to(i)
            # Check if target frame is in the decoded frames (it should be)
            found_target = any(frame_idx == i and frame_data is not None for frame_idx, frame_data in decoded_frames)
            if found_target:
                accessible_frames += 1

        # Sequential count should match metadata exactly
        assert sequential_count == total_frames, f"Sequential read count {sequential_count} != metadata {total_frames}"

        # Should be able to access ALL sampled frames - no failures allowed
        expected_samples = len(range(0, total_frames, step))
        assert accessible_frames == expected_samples, (
            f"Must access all sampled frames: {accessible_frames}/{expected_samples}"
        )


class TestResourceManagement:
    """Test resource management and cleanup."""

    def test_close_method(self, video_file_factory: Callable[[float, int], Path]) -> None:
        """Test closing resources."""
        video_path = video_file_factory(1.0, 30)

        reader = PyAvAbstraction(str(video_path))

        # Should close without error
        reader.close()

        # Calling close multiple times should be safe
        reader.close()


class TestRealWorldScenarios:
    """Test real-world usage scenarios."""

    @pytest.fixture
    def long_reader(self, video_file_factory: Callable[[float, int], Path]) -> Generator[PyAvAbstraction, None, None]:
        """Create reader with longer video for comprehensive testing."""
        video_path = video_file_factory(5.0, 30)
        reader = PyAvAbstraction(str(video_path))
        yield reader
        reader.close()

    def test_read_seek_read_pattern(self, long_reader: PyAvAbstraction) -> None:
        """Test common pattern: read some frames, seek, read more."""
        # Read first 10 frames
        for _ in range(10):
            frame = long_reader.read_next()
            assert frame is not None

        assert long_reader.current_frame_index == 10

        # Seek to middle
        total_frames = long_reader.get_total_frames()
        middle = total_frames // 2
        long_reader.jump_to(middle)

        # Read 5 more frames from current position
        start_position = long_reader.current_frame_index
        for i in range(5):
            frame = long_reader.read_next()
            assert frame is not None
            assert long_reader.current_frame_index == start_position + i + 1

    def test_random_access_pattern(self, long_reader: PyAvAbstraction) -> None:
        """Test random access to different parts of video."""
        total_frames = long_reader.get_total_frames()

        # Test random positions, avoiding very end
        test_positions = [0, total_frames // 4, total_frames // 2, 3 * total_frames // 4, total_frames - 20]

        for pos in test_positions:
            long_reader.jump_to(pos)
            position_before_read = long_reader.current_frame_index
            frame = long_reader.read_next()
            assert frame is not None
            assert long_reader.current_frame_index == position_before_read + 1

    def test_full_video_read(self, temp_dir: Path) -> None:
        """Test reading an entire video from start to finish."""
        video_path = temp_dir / "full_read_test.mp4"
        create_test_video(video_path, duration=2.0, fps=15)  # Smaller for faster test

        reader = PyAvAbstraction(str(video_path))
        total_frames = reader.get_total_frames()

        frames_read = 0
        while True:
            video_frame = reader.read_next()
            if video_frame is None:
                break
            frames_read += 1

            # Verify frame properties
            assert isinstance(video_frame, av.VideoFrame)

            # Convert to numpy for testing
            frame = video_frame.to_ndarray(format="rgb24")
            assert frame is not None
            assert isinstance(frame, np.ndarray)
            assert frame.shape[2] == 3  # RGB24

        # Should read all frames when reading entire video
        assert frames_read == total_frames, f"Expected to read all {total_frames} frames, got {frames_read}"
        assert reader.current_frame_index == frames_read

        reader.close()


class TestErrorConditions:
    """Test error conditions and edge cases."""

    def test_nonexistent_video_file(self, temp_dir: Path) -> None:
        """Test initialization with nonexistent video file."""
        nonexistent_path = temp_dir / "nonexistent.mp4"

        with pytest.raises((FileNotFoundError, OSError)):
            PyAvAbstraction(str(nonexistent_path))

    def test_invalid_video_file(self, temp_dir: Path) -> None:
        """Test initialization with invalid video file."""
        invalid_path = temp_dir / "invalid.mp4"
        invalid_path.write_text("This is not a video file")

        with pytest.raises((av.error.InvalidDataError)):
            PyAvAbstraction(str(invalid_path))

    def test_read_after_end_of_stream(self, temp_dir: Path) -> None:
        """Test reading after reaching end of stream."""
        video_path = temp_dir / "end_test.mp4"
        create_test_video(video_path, duration=0.1, fps=10)  # Very short video

        reader = PyAvAbstraction(str(video_path))

        # Read all frames
        while reader.read_next() is not None:
            pass

        # Further reads should return None
        assert reader.read_next() is None
        assert reader.read_next() is None

        reader.close()
