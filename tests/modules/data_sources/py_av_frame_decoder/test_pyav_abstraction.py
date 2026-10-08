"""Sequential decode, seek accuracy and input failures at the PyAV boundary."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import av
import numpy as np
import pytest
from numpy.typing import NDArray

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import PyAvAbstraction
from tests.helpers.video import create_test_video


def test_read_complete_video_and_rewind(video_file_factory: Callable[[float, int], Path]) -> None:
    """Read every frame exactly once, reach stable EOF, and rewind without losing frame zero."""
    with PyAvAbstraction(str(video_file_factory(1.0, 30))) as reader:
        assert reader.get_total_frames() == 30
        assert reader.current_frame_index == 0
        pixels: list[NDArray[Any]] = []
        for index in range(30):
            frame = reader.read_next()
            assert frame is not None
            rgb = frame.to_ndarray(format="rgb24")
            assert rgb.shape == (240, 320, 3)
            assert rgb.dtype == np.uint8
            assert np.var(rgb) > 0
            if pixels:
                assert not np.array_equal(pixels[-1], rgb)
            pixels.append(rgb)
            assert reader.current_frame_index == index + 1
        assert reader.read_next() is None
        assert reader.read_next() is None
        assert reader.current_frame_index == 30
        rewound = dict(reader.jump_to(0))
        np.testing.assert_array_equal(rewound[0].to_ndarray(format="rgb24"), pixels[0])
        assert reader.current_frame_index == 1
    reader.close()  # Repeated cleanup is safe after context-manager exit.


@pytest.fixture(scope="module")
def mp4_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Provide the MP4 control with the same long GOP as the ASF regression fixture."""
    path = tmp_path_factory.mktemp("mp4-seek") / "test.mp4"
    create_test_video(path, duration=10.0, fps=25, gop=60)
    return path


@pytest.mark.parametrize("container", ["mp4", "asf"])
def test_seek_and_following_frames_match_sequential_decode(mp4_video: Path, asf_video: Path, container: str) -> None:
    """Long-GOP seeks, including ASF PTS offsets, preserve pixels and the subsequent read position."""
    path = {"mp4": mp4_video, "asf": asf_video}[container]
    with PyAvAbstraction(str(path)) as reader:
        expected = []
        for _ in range(250):
            frame = reader.read_next()
            assert frame is not None
            expected.append(frame.to_ndarray(format="rgb24"))
        assert reader.read_next() is None
        assert reader.get_total_frames() == len(expected)

        # Cross keyframes in both directions, return to frame zero, and read through EOF.
        for target in (62, 83, 125, 187, 0, 245, 240, 230, 249):
            decoded = dict(reader.jump_to(target))
            assert target in decoded
            for index, frame in decoded.items():
                np.testing.assert_array_equal(frame.to_ndarray(format="rgb24"), expected[index])
            # A decoder packet may contain several frames, particularly its final flush packet.
            next_index = max(decoded) + 1
            assert reader.current_frame_index == next_index
            for index in range(next_index, min(next_index + 10, len(expected))):
                following = reader.read_next()
                assert following is not None
                np.testing.assert_array_equal(following.to_ndarray(format="rgb24"), expected[index])
                assert reader.current_frame_index == index + 1
        assert reader.read_next() is None


def test_invalid_seek_preserves_read_position(video_file_factory: Callable[[float, int], Path]) -> None:
    """Rejected indices leave the next valid frame available."""
    with PyAvAbstraction(str(video_file_factory(1.0, 30))) as reader:
        assert reader.read_next() is not None
        for index in (-1, 30, 130):
            with pytest.raises(ValueError, match="out of bounds"):
                reader.jump_to(index)
            assert reader.current_frame_index == 1
        assert reader.read_next() is not None
        assert reader.current_frame_index == 2


def test_missing_and_invalid_media_are_rejected(tmp_path: Path) -> None:
    """Missing files and non-video bytes fail at open with their underlying media error."""
    path = tmp_path / "invalid.mp4"
    with pytest.raises((FileNotFoundError, OSError)):
        PyAvAbstraction(str(path))
    path.write_text("This is not a video file")
    with pytest.raises(av.error.InvalidDataError):
        PyAvAbstraction(str(path))
