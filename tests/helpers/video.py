"""Video fixtures and helpers for tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import av

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.cached_frame import CachedFrame


def create_test_video(path: Path, duration: float = 1.0, fps: int = 30) -> None:
    """Create a small H.264 test video using ffmpeg's test source."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:rate={fps}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-keyint_min",
        "10",
        "-g",
        "10",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def make_cached_frame(index: int = 0, *, width: int = 16, height: int = 16) -> CachedFrame:
    """Create a real RGB frame with predictable byte reservation for cache tests."""
    return CachedFrame(
        frame_index=index,
        video_frame=av.VideoFrame(width, height, "rgb24"),
        timestamp_us=float(index * 1000),
        period_after_s=0.001,
        source_timing_metadata={},
    )
