"""Video fixtures and helpers for tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import av
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.cached_frame import CachedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import FrameReaderWorker


def create_test_video(
    path: Path,
    duration: float = 1.0,
    fps: int = 30,
    *,
    gop: int = 10,
    b_frames: int = 0,
    container: str | None = None,
) -> None:
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
        str(gop),
        "-g",
        str(gop),
        "-bf",
        str(b_frames),
    ]
    if container is not None:
        cmd.extend(["-f", container])
    cmd.append(str(path))
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


def drain_prefetch(worker: FrameReaderWorker, limit: int = 100) -> None:
    """Run prefetch steps until the worker reports it has nothing more to fetch; fail if it never stops."""
    for _ in range(limit):
        if not worker.prefetch_one():
            return
    pytest.fail("Prefetch did not stop")


def cached_frames(worker: FrameReaderWorker) -> list[int]:
    """Return the index of every frame in the worker's cache."""
    return [index for first, last in worker.get_cached_ranges() for index in range(first, last + 1)]
