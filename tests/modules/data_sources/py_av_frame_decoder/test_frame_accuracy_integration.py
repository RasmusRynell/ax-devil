"""Compare decoder output with independent ffmpeg reference pixels."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import VideoFrameReader


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
    [tuple(range(20)), (0, 10, 25, 5, 35, 15), (0, 59, 1, 58)],
    ids=["sequential", "forward-backward-jumps", "first-last-frames"],
)
def test_frames_match_ffmpeg(video_and_references: tuple[Path, Path], indices: tuple[int, ...]) -> None:
    """Every requested frame retains its exact index and reference pixels."""
    video, references = video_and_references
    reader = VideoFrameReader(str(video), cache_budget_bytes=30 * 320 * 240 * 3, prefetch_count=10)
    try:
        reader.open()
        for index in indices:
            decoded = reader.get_frame_sync(index, timeout=10.0)
            assert decoded is not None, f"Failed to decode frame {index}"
            assert decoded.frame_index == index
            reference = cv2.imread(str(references / f"frame_{index + 1:04d}.png"))
            assert reference is not None, f"Failed to load reference frame {index}"
            np.testing.assert_array_equal(decoded.pixels, cv2.cvtColor(reference, cv2.COLOR_BGR2RGB))
    finally:
        reader.close()
