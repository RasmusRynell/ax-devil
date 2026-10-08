"""Tests for video_analyzer module - real functionality testing only."""

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest.mock import patch

import pytest

from ax_devil.modules.cache import CacheManager
from ax_devil.modules.data_sources.video_analyzer import Fingerprint, VideoAnalyzer


@pytest.fixture(autouse=True)
def isolated_cache(temp_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real cache manager within this test's temporary directory."""
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: temp_dir / "cache")


@pytest.fixture
def video_path(video_file_factory: Callable[[float, int], Path]) -> Path:
    """Reuse source bytes while each test has a separate metadata cache."""
    return video_file_factory(1.0, 30)


class TestFingerprint:
    """Test fingerprint functionality with real files."""

    def test_fingerprint_creation(self, temp_dir: Path) -> None:
        """Test creating fingerprints from real files."""
        test_file = temp_dir / "test.txt"
        test_file.write_text("test content")

        fp = Fingerprint.from_path(test_file)

        assert fp.size == len("test content")
        assert fp.inode > 0
        assert fp.mtime_ns > 0
        assert fp.as_tuple() == (fp.inode, fp.size, fp.mtime_ns)

    def test_fingerprint_detects_changes(self, temp_dir: Path) -> None:
        """Test that fingerprints change when files change."""
        test_file = temp_dir / "test.txt"
        test_file.write_text("original")

        fp1 = Fingerprint.from_path(test_file)

        # Modify file with significantly different content
        test_file.write_text("completely different and much longer content")

        fp2 = Fingerprint.from_path(test_file)

        assert fp1.size != fp2.size  # Different content lengths


class TestVideoAnalyzer:
    """Test VideoAnalyzer with real video files and tools."""

    @pytest.fixture
    def analyzer(self) -> VideoAnalyzer:
        """Create analyzer using an isolated writable cache."""
        return VideoAnalyzer()

    def test_analyze_real_video(
        self, analyzer: VideoAnalyzer, video_file_factory: Callable[[float, int], Path]
    ) -> None:
        """Test analyzing a real video file."""
        video_path = video_file_factory(2.0, 25)

        result = analyzer.analyze(video_path)

        # Should get real metadata
        assert result["frame_count"] == 50
        assert result["fps"] == 25
        assert (result["width"], result["height"]) == (320, 240)
        assert result["duration_sec"] == pytest.approx(2.0)
        assert result["source"] == "ffprobe"

    def test_caching_works(self, analyzer: VideoAnalyzer, video_path: Path) -> None:
        """Test that caching actually works."""
        # First analysis - should probe video
        result1 = analyzer.analyze(video_path)
        cache_file = analyzer._get_cache_path(video_path)

        # Cache file should exist
        assert cache_file.exists()

        # Second analysis - should load from cache
        with (
            patch.object(analyzer, "_run", side_effect=AssertionError("Cache hit must not run external probes")),
            patch.object(analyzer, "_pyav_probe", side_effect=AssertionError("Cache hit must not decode frames")),
        ):
            result2 = analyzer.analyze(video_path)

        # Should get same data
        assert result2 == {**result1, "source": f"{result1['source']} (cache)"}

    def test_cache_invalidation(
        self, analyzer: VideoAnalyzer, temp_dir: Path, video_file_factory: Callable[[float, int], Path]
    ) -> None:
        """Test that cache gets invalidated when file changes."""
        video_path = temp_dir / "test.mp4"
        shutil.copyfile(video_file_factory(1.0, 30), video_path)

        # Analyze and cache
        result1 = analyzer.analyze(video_path)

        # Replace with significantly different video
        shutil.copyfile(video_file_factory(3.0, 10), video_path)

        # Should analyze again (not use stale cache)
        result2 = analyzer.analyze(video_path)

        assert result1["duration_sec"] == pytest.approx(1.0)
        assert result2["duration_sec"] == pytest.approx(3.0)
        assert result2["fps"] == pytest.approx(10.0)
        assert "(cache)" not in result2["source"]

    def test_refresh_bypasses_cache(self, analyzer: VideoAnalyzer, video_path: Path) -> None:
        """Test that refresh=True bypasses cache."""
        # First analysis
        result1 = analyzer.analyze(video_path)

        # Second with refresh - should not use cache
        result2 = analyzer.analyze(video_path, refresh=True)

        # Should get same data but without cache indication
        assert result1["frame_count"] == result2["frame_count"]
        assert "(cache)" not in result2["source"]

    @pytest.mark.parametrize("all_tools_fail", [False, True], ids=["later-ffprobe-strategy", "pyav-fallback"])
    def test_probe_failures_fall_back(self, analyzer: VideoAnalyzer, video_path: Path, all_tools_fail: bool) -> None:
        """Failed external probes still produce complete metadata through a later strategy."""
        original_run = analyzer._run

        def run(command: Sequence[str]) -> str:
            if all_tools_fail or "-count_frames" not in command:
                raise RuntimeError("probe unavailable")
            return original_run(command)

        with patch.object(analyzer, "_run", side_effect=run):
            result = analyzer.analyze(video_path)
        assert result == {
            "source": "pyav" if all_tools_fail else "ffprobe -count_frames",
            "frame_count": 30,
            "fps": 30.0,
            "width": 320,
            "height": 240,
            "duration_sec": 1.0,
        }

    def test_ffprobe_strategies(self, analyzer: VideoAnalyzer, video_path: Path) -> None:
        """Test different ffprobe strategies directly."""
        # Test basic ffprobe
        result1 = analyzer._ffprobe_stream(video_path)
        assert result1 is not None
        assert result1["frame_count"] > 0

        # Test with count_frames
        result2 = analyzer._ffprobe_stream(video_path, count_frames=True)
        assert result2 is not None
        assert "count_frames" in result2["source"]

        # Test with deep analysis
        result3 = analyzer._ffprobe_stream(video_path, deep=True)
        assert result3 is not None
        assert "deep" in result3["source"]

    def test_ffmpeg_counting(self, analyzer: VideoAnalyzer, video_path: Path) -> None:
        """Test ffmpeg frame counting directly."""
        result = analyzer._ffmpeg_count(video_path)

        assert result is not None
        assert result["source"] == "ffmpeg-null"
        assert result["frame_count"] == 30

    def test_error_handling(self, analyzer: VideoAnalyzer, temp_dir: Path) -> None:
        """Test error handling with invalid files."""
        # Nonexistent file
        with pytest.raises(FileNotFoundError):
            analyzer.analyze(temp_dir / "nonexistent.mp4")

        # Invalid video file
        invalid_path = temp_dir / "not_video.txt"
        invalid_path.write_text("This is not a video")

        # Should try all strategies and fail gracefully
        with pytest.raises(RuntimeError, match="All probing methods failed"):
            analyzer.analyze(invalid_path)

    def test_cache_corruption_handling(self, analyzer: VideoAnalyzer, video_path: Path) -> None:
        """Test handling of corrupted cache files."""
        # Create corrupted cache file
        cache_path = analyzer._get_cache_path(video_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("invalid json")

        # Should handle gracefully and re-analyze
        result = analyzer.analyze(video_path)
        assert result is not None
        assert result["frame_count"] > 0


class TestRealWorldUsage:
    """Test real-world usage patterns."""

    def test_analyzing_multiple_videos(self, video_file_factory: Callable[[float, int], Path]) -> None:
        """Test analyzing multiple different videos."""
        analyzer = VideoAnalyzer()

        # Create different videos
        video1 = video_file_factory(1.0, 30)
        video2 = video_file_factory(4.0, 15)

        # Analyze both
        result1 = analyzer.analyze(video1)
        result2 = analyzer.analyze(video2)

        # Should have different metadata
        assert result1["frame_count"] != result2["frame_count"]
        assert result1["fps"] != result2["fps"]
        assert result1["duration_sec"] != result2["duration_sec"]

        # Both should have valid data
        for result in [result1, result2]:
            assert result["frame_count"] > 0
            assert result["fps"] > 0
            assert result["duration_sec"] > 0

    def test_cache_persistence(self, video_path: Path) -> None:
        """Test that cache persists across analyzer instances."""
        # First analyzer instance
        analyzer1 = VideoAnalyzer()
        result1 = analyzer1.analyze(video_path)

        # Second analyzer instance (simulates restart)
        analyzer2 = VideoAnalyzer()
        result2 = analyzer2.analyze(video_path)

        # Should use cached data
        assert "(cache)" in result2["source"]
        assert result1["frame_count"] == result2["frame_count"]
