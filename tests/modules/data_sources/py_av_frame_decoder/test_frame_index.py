"""Frame index: display order, PTS timing and the persisted sidecar cache."""

import shutil
import struct
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

import av
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder import FrameIndex
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_index import (
    _CACHE_MAGIC,
    _HEADER_SIZE,
    _STRUCT_FMT,
)
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import PyAvAbstraction
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import VideoOpenConfig
from tests.helpers.video import create_test_video


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

    def test_frame_indices_follow_decoded_display_order_for_b_frames(self, temp_dir: Path) -> None:
        """Frame indices must follow decoded display order for B-frame videos."""
        video_path = temp_dir / "b_frames.mp4"
        create_test_video(video_path, duration=4.0, fps=30, gop=30, b_frames=3)

        frame_index = FrameIndex(video_path)
        frame_index.build()

        with av.open(str(video_path)) as container:
            stream = container.streams.video[0]
            decoded_pts = [frame.pts for frame in container.decode(stream) if frame.pts is not None]

        assert _packet_pts(video_path) != decoded_pts, "Test video must reorder frames"
        assert frame_index.get_total_frames() == len(decoded_pts)
        assert [frame_index.get_frame_pts(index) for index in range(len(decoded_pts))] == decoded_pts

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
        metadata = frame_index.get_frame_source_timing_metadata(2)
        assert (metadata["video_pts"], metadata["video_pts_delta"], metadata["video_time_base"]) == (120, 120, "1/1000")

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

    def test_saved_index_reloads_for_an_unchanged_source(self, frame_index: FrameIndex) -> None:
        """A missing or empty cache is not loaded; a saved index reloads with identical timing and keyframes."""
        assert frame_index.load_from_file() is False
        frame_index.save_to_file()  # Nothing indexed yet, so nothing is written.
        assert frame_index.cache_path is not None
        assert not frame_index.cache_path.exists()

        frame_index.build()
        assert frame_index.get_total_frames() == 60
        frame_index.save_to_file()

        reloaded = FrameIndex(frame_index.video_path, frame_index.cache_path)
        assert reloaded.load_from_file() is True
        assert reloaded.pts_list == frame_index.pts_list
        assert reloaded.is_key_list == frame_index.is_key_list
        assert reloaded.get_frame_time_us(59) == frame_index.get_frame_time_us(59)

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

        frame_index.pts_list[2] = 2**64  # Fails after the header and the first records are written.
        with pytest.raises(struct.error):
            frame_index.save_to_file()

        assert set(frame_index.cache_path.parent.iterdir()) == original_files
        if existing_cache:
            assert frame_index.cache_path.read_bytes() == original_data
            assert frame_index.load_from_file() is True
        else:
            assert not frame_index.cache_path.exists()

    def test_cache_fingerprint_mismatch_is_ignored(self, frame_index: FrameIndex) -> None:
        """Frame index cache should not be reused after the source file changes."""
        frame_index.build()
        frame_index.save_to_file()

        frame_index.video_path.write_bytes(b"changed")

        frame_index2 = FrameIndex(frame_index.video_path, frame_index.cache_path)
        assert frame_index2.load_from_file() is False

    def test_image_sequence_cache_tracks_its_frame_files(self, temp_dir: Path) -> None:
        """Image sequence caches fingerprint the matching files, not the printf pattern."""
        image_dir = temp_dir / "img1"
        image_dir.mkdir()
        first_frame = image_dir / "000001.jpg"
        first_frame.write_bytes(b"frame-one")
        (image_dir / "000002.jpg").write_bytes(b"frame-two")
        pattern_path = image_dir / "%06d.jpg"
        cache_path = temp_dir / "sequence.ptsidx"
        open_config = VideoOpenConfig(av_format="image2", av_options={"framerate": "30.0", "start_number": "1"})

        frame_index = FrameIndex(pattern_path, cache_path, open_config=open_config)
        frame_index.time_base = Fraction(1, 30)
        frame_index.pts_list = [0, 1]
        frame_index.is_key_list = [True, True]
        frame_index.save_to_file()

        cached_index = FrameIndex(pattern_path, cache_path, open_config=open_config)
        assert cached_index.load_from_file() is True
        assert cached_index.pts_list == [0, 1]

        first_frame.write_bytes(b"changed-frame-one")
        assert FrameIndex(pattern_path, cache_path, open_config=open_config).load_from_file() is False
