"""Tests for frame_index.py module - real functionality testing only."""

import shutil
import struct
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import av
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder import FrameIndex
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_index import (
    _CACHE_MAGIC,
    _CACHE_VERSION,
    _HEADER_FMT,
    _HEADER_SIZE,
    _STRUCT_FMT,
)
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import PyAvAbstraction
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import VideoOpenConfig
from tests.helpers.video import create_test_video


def _create_b_frame_video(path: Path) -> None:
    """Create a video with B-frames so packet order can differ from display order."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=4:rate=30:size=320x240",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-bf",
        "3",
        "-g",
        "30",
        "-keyint_min",
        "30",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _packet_pts(video_path: Path) -> list[int]:
    """Return packet PTS order for test assertions."""
    with av.open(str(video_path), mode="r") as container:
        stream = container.streams.video[0]
        return [packet.pts for packet in container.demux(stream) if packet.pts is not None]


class TestFrameIndex:
    """Test FrameIndex functionality with real video files."""

    @pytest.fixture
    def video_path(self, temp_dir: Path, video_file_factory: Callable[[float, int], Path]) -> Path:
        """Copy shared media so source-invalidation cases can safely overwrite it."""
        video_path = temp_dir / "test.mp4"
        shutil.copyfile(video_file_factory(2.0, 30), video_path)
        return video_path

    @pytest.fixture
    def frame_index(self, video_path: Path, temp_dir: Path) -> FrameIndex:
        """Create a FrameIndex instance with cache path."""
        cache_path = temp_dir / f"{video_path.name}.ptsidx"
        return FrameIndex(video_path, cache_path)

    def test_build_real_video(self, frame_index: FrameIndex) -> None:
        """Test building a presentation-order frame index from a real video."""
        frame_index.build()

        # Should have indexed frames
        assert len(frame_index.pts_list) > 0
        assert len(frame_index.is_key_list) == len(frame_index.pts_list)

        # PTS values should be increasing
        assert frame_index.pts_list == sorted(frame_index.pts_list)

        # Should have keyframes
        assert any(frame_index.is_key_list)

        assert frame_index.time_base is not None

        # pts_to_index should be populated
        assert len(frame_index.pts_to_index) == len(frame_index.pts_list)

        for i, pts in enumerate(frame_index.pts_list):
            assert frame_index.pts_to_index[pts] == i

    def test_frame_indices_follow_decoded_display_order_for_b_frames(self, temp_dir: Path) -> None:
        """Frame indices must follow decoded display order for B-frame videos."""
        video_path = temp_dir / "b_frames.mp4"
        _create_b_frame_video(video_path)

        frame_index = FrameIndex(video_path)
        frame_index.build()

        with av.open(str(video_path)) as container:
            stream = container.streams.video[0]
            decoded_pts = [frame.pts for frame in container.decode(stream) if frame.pts is not None]

        assert decoded_pts
        assert _packet_pts(video_path) != decoded_pts
        assert len(frame_index.pts_list) == len(decoded_pts)
        for expected_index, pts in enumerate(decoded_pts):
            assert frame_index.pts_to_index[pts] == expected_index

    def test_get_total_frames(self, frame_index: FrameIndex) -> None:
        """Test getting total frame count."""
        assert frame_index.get_total_frames() == 0

        frame_index.build()
        total_frames = frame_index.get_total_frames()

        assert total_frames > 0
        assert total_frames == len(frame_index.pts_list)

    def test_get_frame_pts(self, frame_index: FrameIndex) -> None:
        """Test getting PTS for frame indices."""
        frame_index.build()

        # Test valid indices
        for i in range(len(frame_index.pts_list)):
            pts = frame_index.get_frame_pts(i)
            assert pts == frame_index.pts_list[i]

        # Test invalid indices
        with pytest.raises(IndexError):
            frame_index.get_frame_pts(-1)

        with pytest.raises(IndexError):
            frame_index.get_frame_pts(len(frame_index.pts_list))

    def test_frame_times_follow_pts_not_nominal_fps(self, video_path: Path) -> None:
        """Frame times should come from PTS deltas, not a single nominal FPS value."""
        frame_index = FrameIndex(video_path)
        frame_index.time_base = Fraction(1, 1000)
        frame_index.pts_list = [0, 40, 120, 200, 240]
        frame_index.is_key_list = [True, False, False, False, False]
        frame_index.pts_to_index = {pts: i for i, pts in enumerate(frame_index.pts_list)}

        assert frame_index.get_frame_time_us(0) == 0.0
        assert frame_index.get_frame_time_us(1) == 40_000.0
        assert frame_index.get_frame_time_us(2) == 120_000.0
        assert frame_index.get_frame_period_after_s(0) == 0.04
        assert frame_index.get_frame_period_after_s(1) == 0.08
        assert frame_index.get_frame_period_after_s(3) == 0.04
        assert frame_index.get_frame_period_after_s(4) == 0.04
        assert frame_index.get_frame_source_timing_metadata(2) == {
            "video_timestamp_source": "pts_time_base_minus_first_pts",
            "video_pts": 120,
            "video_first_pts": 0,
            "video_pts_delta": 120,
            "video_time_base": "1/1000",
            "video_time_base_numerator": 1,
            "video_time_base_denominator": 1000,
        }

    def test_analyze_timing_profile_detects_variable_cadence(self, video_path: Path) -> None:
        """Timing profile should summarize multiple decoded PTS periods."""
        frame_index = FrameIndex(video_path)
        frame_index.time_base = Fraction(1, 1000)
        frame_index.pts_list = [0, 80, 120, 200, 280, 320]
        frame_index.is_key_list = [True, False, False, False, False, False]
        frame_index.pts_to_index = {pts: i for i, pts in enumerate(frame_index.pts_list)}

        profile = frame_index.analyze_timing_profile()

        assert profile.total_frames == 6
        assert profile.is_variable_cadence is True
        assert dict(profile.period_modes_us) == {80_000: 3, 40_000: 2}

    def test_analyze_timing_profile_detects_constant_cadence(self, video_path: Path) -> None:
        """Timing profile should report constant cadence when all PTS periods match."""
        frame_index = FrameIndex(video_path)
        frame_index.time_base = Fraction(1, 1000)
        frame_index.pts_list = [0, 40, 80, 120]
        frame_index.is_key_list = [True, False, False, False]
        frame_index.pts_to_index = {pts: i for i, pts in enumerate(frame_index.pts_list)}

        profile = frame_index.analyze_timing_profile()

        assert profile.is_variable_cadence is False
        assert profile.period_modes_us == ((40_000, 3),)

    def test_nonmonotonic_pts_period_returns_none(self, video_path: Path) -> None:
        """Broken PTS transitions should be visible so callers can fall back to FPS."""
        frame_index = FrameIndex(video_path)
        frame_index.time_base = Fraction(1, 1000)
        frame_index.pts_list = [0, 40, 40, 30]
        frame_index.is_key_list = [True, False, False, False]

        assert frame_index.get_frame_period_after_s(0) == 0.04
        assert frame_index.get_frame_period_after_s(1) is None
        assert frame_index.get_frame_period_after_s(2) is None

    def test_is_keyframe(self, frame_index: FrameIndex) -> None:
        """Test keyframe detection."""
        frame_index.build()

        # Test valid indices
        for i in range(len(frame_index.is_key_list)):
            is_key = frame_index.is_keyframe(i)
            assert is_key == frame_index.is_key_list[i]

        # Test invalid indices
        assert frame_index.is_keyframe(-1) is False
        assert frame_index.is_keyframe(len(frame_index.is_key_list)) is False

    def test_get_nearest_keyframe_before(self, frame_index: FrameIndex) -> None:
        """Test finding nearest keyframe before a given index."""
        frame_index.build()

        assert any(frame_index.is_key_list), "Generated test video must contain keyframes"

        total_frames = len(frame_index.pts_list)

        # Test valid indices
        for i in range(total_frames):
            keyframe_idx = frame_index.get_nearest_keyframe_before(i)
            assert 0 <= keyframe_idx <= i
            assert frame_index.is_keyframe(keyframe_idx)

        # Test invalid indices
        with pytest.raises(IndexError):
            frame_index.get_nearest_keyframe_before(-1)

        with pytest.raises(IndexError):
            frame_index.get_nearest_keyframe_before(total_frames)

    def test_save_and_load_from_file(self, frame_index: FrameIndex, temp_dir: Path) -> None:
        """Test saving and loading frame index from cache file."""
        # Build index
        frame_index.build()
        original_pts = frame_index.pts_list.copy()
        original_keys = frame_index.is_key_list.copy()

        # Save to file
        frame_index.save_to_file()

        # Check that cache file was created
        assert frame_index.cache_path is not None
        assert frame_index.cache_path.exists()

        # Create new instance and load
        frame_index2 = FrameIndex(frame_index.video_path, frame_index.cache_path)
        success = frame_index2.load_from_file()

        assert success is True
        assert frame_index2.pts_list == original_pts
        assert frame_index2.is_key_list == original_keys
        assert frame_index2.pts_to_index == frame_index.pts_to_index
        assert frame_index2.time_base == frame_index.time_base

    def test_save_empty_index(self, frame_index: FrameIndex) -> None:
        """Test saving empty frame index."""
        # Don't build index, try to save empty
        frame_index.save_to_file()

        # Should not create file for empty index
        assert frame_index.cache_path is not None
        assert not frame_index.cache_path.exists()

    def test_load_nonexistent_file(self, frame_index: FrameIndex) -> None:
        """Test loading from nonexistent sidecar file."""
        success = frame_index.load_from_file()
        assert success is False

    def test_sidecar_file_format(self, frame_index: FrameIndex) -> None:
        """Test the binary format of cache files."""
        frame_index.build()
        frame_index.save_to_file()

        # Read and verify binary format
        assert frame_index.cache_path is not None
        with frame_index.cache_path.open("rb") as f:
            data = f.read()

        expected_size = _HEADER_SIZE + (len(frame_index.pts_list) * struct.calcsize(_STRUCT_FMT))
        assert len(data) == expected_size

        magic, version, numerator, denominator, inode, size, mtime_ns, frame_count = struct.unpack(
            _HEADER_FMT, data[:_HEADER_SIZE]
        )
        assert magic == _CACHE_MAGIC
        assert version == _CACHE_VERSION
        assert frame_count == len(frame_index.pts_list)
        assert Fraction(numerator, denominator) == frame_index.time_base
        assert (inode, size, mtime_ns) == frame_index._fingerprint()

        # Verify we can unpack the data
        for i in range(len(frame_index.pts_list)):
            offset = _HEADER_SIZE + (i * struct.calcsize(_STRUCT_FMT))
            chunk = data[offset : offset + struct.calcsize(_STRUCT_FMT)]
            pts, is_key = struct.unpack(_STRUCT_FMT, chunk)

            assert pts == frame_index.pts_list[i]
            assert bool(is_key) == frame_index.is_key_list[i]

    @pytest.mark.parametrize("damage", ["header", "partial_record", "whole_records", "extra_record", "old_version"])
    def test_incomplete_cache_is_rebuilt(self, frame_index: FrameIndex, damage: str) -> None:
        """Damaged caches must rebuild all source frames and persist a reusable index."""
        frame_index.build()
        expected_pts = frame_index.pts_list.copy()
        frame_index.save_to_file()
        assert frame_index.cache_path is not None
        data = frame_index.cache_path.read_bytes()
        record_size = struct.calcsize(_STRUCT_FMT)
        damaged_data = {
            "header": data[: _HEADER_SIZE - 1],
            "partial_record": data[: _HEADER_SIZE + record_size + 1],
            "whole_records": data[: _HEADER_SIZE + record_size],
            "extra_record": data + data[-record_size:],
            "old_version": struct.pack("<7sBqqQQQ", _CACHE_MAGIC, 2, 1, 1000, *frame_index._fingerprint())
            + data[_HEADER_SIZE:],
        }[damage]
        frame_index.cache_path.write_bytes(damaged_data)

        assert frame_index.load_from_file() is False
        assert frame_index.pts_list == []
        assert frame_index.is_key_list == []
        assert frame_index.pts_to_index == {}
        assert frame_index.time_base is None

        with PyAvAbstraction(str(frame_index.video_path), frame_index.cache_path) as reader:
            assert reader.frame_index.pts_list == expected_pts
            assert reader.get_total_frames() == len(expected_pts)

        assert frame_index.load_from_file() is True
        assert frame_index.pts_list == expected_pts

    @pytest.mark.parametrize("existing_cache", [False, True])
    def test_interrupted_write_does_not_publish_partial_cache(
        self, frame_index: FrameIndex, existing_cache: bool
    ) -> None:
        """Failed writes preserve an existing cache or leave no cache on first save."""
        frame_index.build()
        frame_index.save_to_file()
        assert frame_index.cache_path is not None
        original_data = frame_index.cache_path.read_bytes()
        if not existing_cache:
            frame_index.cache_path.unlink()
        original_files = set(frame_index.cache_path.parent.iterdir())

        with patch(
            "ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_index.struct.pack",
            side_effect=[
                original_data[:_HEADER_SIZE],
                original_data[_HEADER_SIZE : _HEADER_SIZE + struct.calcsize(_STRUCT_FMT)],
                OSError("Interrupted write"),
            ],
        ):
            with pytest.raises(OSError, match="Interrupted write"):
                frame_index.save_to_file()

        assert set(frame_index.cache_path.parent.iterdir()) == original_files
        if existing_cache:
            assert frame_index.cache_path.read_bytes() == original_data
            assert frame_index.load_from_file() is True
        else:
            assert not frame_index.cache_path.exists()

    def test_corrupted_sidecar_file(self, frame_index: FrameIndex, temp_dir: Path) -> None:
        """Test handling of corrupted cache files."""
        frame_index.build()

        # Create unsupported old-format file without the required header
        assert frame_index.cache_path is not None
        with frame_index.cache_path.open("wb") as f:
            f.write(struct.pack(_STRUCT_FMT, 1000, 1))

        frame_index2 = FrameIndex(frame_index.video_path, frame_index.cache_path)
        success = frame_index2.load_from_file()

        assert success is False
        assert frame_index2.pts_list == []

    def test_cache_fingerprint_mismatch_is_ignored(self, frame_index: FrameIndex) -> None:
        """Frame index cache should not be reused after the source file changes."""
        frame_index.build()
        frame_index.save_to_file()

        frame_index.video_path.write_bytes(b"changed")

        frame_index2 = FrameIndex(frame_index.video_path, frame_index.cache_path)
        assert frame_index2.load_from_file() is False

    def test_image_sequence_cache_fingerprints_matching_files(self, temp_dir: Path) -> None:
        """Image sequence caches should fingerprint real image files, not the printf pattern."""
        image_dir = temp_dir / "img1"
        image_dir.mkdir()
        (image_dir / "000001.jpg").write_bytes(b"frame-one")
        (image_dir / "000002.jpg").write_bytes(b"frame-two")
        pattern_path = image_dir / "%06d.jpg"
        cache_path = temp_dir / "sequence.ptsidx"
        open_config = VideoOpenConfig(
            av_format="image2",
            av_options={"framerate": "30.0", "start_number": "1"},
        )

        frame_index = FrameIndex(pattern_path, cache_path, open_config=open_config)
        frame_index.time_base = Fraction(1, 30)
        frame_index.pts_list = [0, 1]
        frame_index.is_key_list = [True, True]
        frame_index.pts_to_index = {0: 0, 1: 1}
        frame_index.save_to_file()

        cached_index = FrameIndex(pattern_path, cache_path, open_config=open_config)

        assert cached_index.load_from_file() is True
        assert cached_index.pts_list == [0, 1]
        assert cached_index.is_key_list == [True, True]

    def test_image_sequence_cache_invalidates_when_matching_file_changes(self, temp_dir: Path) -> None:
        """Image sequence caches should not be reused after a frame file changes."""
        image_dir = temp_dir / "img1"
        image_dir.mkdir()
        first_frame = image_dir / "000001.jpg"
        first_frame.write_bytes(b"frame-one")
        (image_dir / "000002.jpg").write_bytes(b"frame-two")
        pattern_path = image_dir / "%06d.jpg"
        cache_path = temp_dir / "sequence.ptsidx"
        open_config = VideoOpenConfig(
            av_format="image2",
            av_options={"framerate": "30.0", "start_number": "1"},
        )

        frame_index = FrameIndex(pattern_path, cache_path, open_config=open_config)
        frame_index.time_base = Fraction(1, 30)
        frame_index.pts_list = [0, 1]
        frame_index.is_key_list = [True, True]
        frame_index.save_to_file()

        first_frame.write_bytes(b"changed-frame-one")

        cached_index = FrameIndex(pattern_path, cache_path, open_config=open_config)
        assert cached_index.load_from_file() is False


class TestFrameIndexRealWorldUsage:
    """Test real-world usage patterns."""

    def test_video_seeking_workflow(self, temp_dir: Path) -> None:
        """Test typical video seeking workflow."""
        # Create video with known characteristics
        video_path = temp_dir / "seek_test.mp4"
        create_test_video(video_path, duration=3.0, fps=30)

        frame_index = FrameIndex(video_path)
        frame_index.build()

        total_frames = frame_index.get_total_frames()
        assert total_frames > 0

        # Test seeking to various positions
        for position in [0, total_frames // 4, total_frames // 2, total_frames - 1]:
            # Get PTS for position
            pts = frame_index.get_frame_pts(position)
            assert pts >= 0

            # Find nearest keyframe
            keyframe_idx = frame_index.get_nearest_keyframe_before(position)
            assert keyframe_idx <= position
            assert frame_index.is_keyframe(keyframe_idx)

            # Get keyframe PTS
            keyframe_pts = frame_index.get_frame_pts(keyframe_idx)
            assert keyframe_pts <= pts

    def test_binary_search_optimization(self, temp_dir: Path) -> None:
        """Test that binary search for keyframes works efficiently."""
        # Create longer video to have more keyframes
        video_path = temp_dir / "long_video.mp4"
        create_test_video(video_path, duration=5.0, fps=30)

        frame_index = FrameIndex(video_path)
        frame_index.build()

        total_frames = frame_index.get_total_frames()
        assert total_frames >= 10, "Generated five-second video must contain at least ten frames"

        # Test keyframe search at various positions
        test_positions = [0, 1, total_frames // 4, total_frames // 2, 3 * total_frames // 4, total_frames - 1]

        for pos in test_positions:
            keyframe_idx = frame_index.get_nearest_keyframe_before(pos)

            # Verify the result is actually a keyframe
            assert frame_index.is_keyframe(keyframe_idx)

            # Verify it's the nearest one (no closer keyframe exists)
            assert keyframe_idx <= pos

            # If there are frames between keyframe and target, none should be keyframes
            for i in range(keyframe_idx + 1, min(pos + 1, total_frames)):
                if i < total_frames and frame_index.is_keyframe(i):
                    # Found a closer keyframe, which means our result was wrong
                    assert False, f"Found closer keyframe at {i} vs {keyframe_idx} for target {pos}"
