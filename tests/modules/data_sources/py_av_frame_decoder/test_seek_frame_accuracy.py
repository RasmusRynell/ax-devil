"""Test that seeked frames return the same pixels as sequential decode.

Reproduces a bug where jump_to() returns frames offset by ~61 frames
due to incorrect frame counting after seek in _decode_frames_to_target.
The code assumes the first decoded frame after seek is the keyframe it
sought to, but container.seek() can land at an earlier position, causing
all subsequent frame indices to be wrong.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import PyAvAbstraction


def _create_video_with_large_gop(path: Path) -> None:
    """Create test video with large GOP and PTS offset (like ASF containers).

    Key properties that trigger the bug:
    - Large GOP (60 frames between keyframes)
    - PTS offset from 0 (itsoffset shifts timestamps)
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=10:rate=25:size=320x240",
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


def _create_standard_video(path: Path) -> None:
    """Create a standard mp4 video (no PTS offset) as control."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=10:rate=25:size=320x240",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "60",
        "-keyint_min",
        "60",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def asf_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """ASF container video with PTS offset — triggers the bug."""
    p = tmp_path_factory.mktemp("asf") / "test.asf"
    _create_video_with_large_gop(p)
    return p


@pytest.fixture()
def mp4_video(tmp_path: Path) -> Path:
    """Standard MP4 video — control case."""
    p = tmp_path / "test.mp4"
    _create_standard_video(p)
    return p


def _read_all_sequential(video_path: Path) -> dict[int, np.ndarray]:
    """Read every frame sequentially → {frame_index: pixels}."""
    frames: dict[int, np.ndarray] = {}
    with PyAvAbstraction(str(video_path)) as reader:
        idx = 0
        while True:
            vf = reader.read_next()
            if vf is None:
                break
            frames[idx] = vf.to_ndarray(format="rgb24").copy()
            idx += 1
    return frames


def _read_frame_via_jump(video_path: Path, frame_index: int) -> np.ndarray:
    """Read a single frame via jump_to → its pixels."""
    with PyAvAbstraction(str(video_path)) as reader:
        decoded = reader.jump_to(frame_index)
        target = {idx: f for idx, f in decoded}
        assert frame_index in target, f"jump_to({frame_index}) did not return target frame"
        return cast(np.ndarray, target[frame_index].to_ndarray(format="rgb24").copy())


def _find_offset(jumped: np.ndarray, sequential: dict[int, np.ndarray], target: int) -> int | None:
    """Find which sequential frame the jumped pixels actually match."""
    for offset in range(-100, 100):
        check = target + offset
        if check in sequential and np.array_equal(jumped, sequential[check]):
            return offset
    return None


class TestSeekFrameAccuracy:
    """Verify seeked frames produce identical pixels to sequential decode."""

    def test_mp4_seek_is_accurate(self, mp4_video: Path) -> None:
        """Control: standard MP4 seek should be accurate."""
        sequential = _read_all_sequential(mp4_video)
        target = len(sequential) // 2
        jumped = _read_frame_via_jump(mp4_video, target)
        assert np.array_equal(jumped, sequential[target])

    def test_asf_seek_is_accurate(self, asf_video: Path) -> None:
        """Bug case: ASF container seek must also be accurate."""
        sequential = _read_all_sequential(asf_video)
        total = len(sequential)

        # Test several non-keyframe targets
        targets = [total // 4, total // 3, total // 2, total * 3 // 4]

        for target in targets:
            jumped = _read_frame_via_jump(asf_video, target)
            if not np.array_equal(jumped, sequential[target]):
                offset = _find_offset(jumped, sequential, target)
                if offset is not None:
                    pytest.fail(
                        f"Frame {target}: seek returned pixels from frame {target + offset} "
                        f"(offset={offset}). _decode_frames_to_target miscounts after seek."
                    )
                else:
                    pytest.fail(f"Frame {target}: seek returned unknown pixels (no match ±100)")

    def test_asf_jump_to_start_returns_first_frame(self, asf_video: Path) -> None:
        """ASF jump_to(0) should return the same first frame as sequential decode."""
        sequential = _read_all_sequential(asf_video)

        jumped = _read_frame_via_jump(asf_video, 0)

        assert np.array_equal(jumped, sequential[0])

    def test_post_seek_sequential_matches(self, asf_video: Path) -> None:
        """After jump, read_next() must also return correct frames."""
        sequential = _read_all_sequential(asf_video)
        total = len(sequential)
        target = total // 2

        with PyAvAbstraction(str(asf_video)) as reader:
            reader.jump_to(target)

            for i in range(10):
                idx = target + 1 + i
                assert idx < total
                vf = reader.read_next()
                assert vf is not None
                pixels = vf.to_ndarray(format="rgb24")
                if not np.array_equal(pixels, sequential[idx]):
                    offset = _find_offset(pixels, sequential, idx)
                    msg = f"offset={offset}" if offset is not None else "no match"
                    pytest.fail(f"Post-seek frame {idx}: wrong pixels ({msg})")
