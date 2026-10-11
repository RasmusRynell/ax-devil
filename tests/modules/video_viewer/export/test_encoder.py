"""Tests for video export encoder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import av
import pytest
from PySide6.QtGui import QImage

from ax_devil.modules.video_viewer.export.encoder import VideoEncoder


def _make_frame(width: int = 100, height: int = 80, color: int = 0xFFFF0000) -> QImage:
    """Create a solid-color QImage."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(color)
    return image


def test_output_has_every_frame_at_the_requested_resolution(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"
    encoder = VideoEncoder(output, width=200, height=150, fps=25.0)
    for index in range(10):
        encoder.write_frame(_make_frame(200, 150), timestamp_us=index * 40_000)
    encoder.finish()

    with av.open(str(output)) as container:
        frames = list(container.decode(video=0))
    assert len(frames) == 10
    assert {(frame.width, frame.height) for frame in frames} == {(200, 150)}


def test_preserves_variable_frame_timestamps_and_last_duration(tmp_path: Path) -> None:
    """Encoded output retains a long source gap instead of compressing the timeline."""
    output = tmp_path / "variable.mp4"
    encoder = VideoEncoder(output, width=100, height=80, fps=25.0)
    for timestamp, duration in [(5_000_000, 0.04), (5_040_000, 0.2), (5_240_000, 0.08)]:
        encoder.write_frame(_make_frame(), timestamp_us=timestamp, duration_s=duration)
    encoder.finish()

    with av.open(str(output)) as container:
        frames = list(container.decode(video=0))
        assert [round(frame.time * 1_000_000) for frame in frames] == [0, 40_000, 240_000]
        time_base = frames[-1].time_base
        assert time_base is not None
        assert round(frames[-1].duration * time_base * 1_000_000) == 80_000


def test_preserves_fractional_frame_rate(tmp_path: Path) -> None:
    """Fractional-rate frame times survive a real encode/decode round trip."""
    output = tmp_path / "fractional.mp4"
    fps = 30_000 / 1001
    encoder = VideoEncoder(output, width=100, height=80, fps=fps)
    timestamps = [index * 1_000_000 / fps for index in range(30)]
    for timestamp in timestamps:
        encoder.write_frame(_make_frame(), timestamp_us=timestamp)
    encoder.finish()

    with av.open(str(output)) as container:
        actual = [round(frame.time * 1_000_000) for frame in container.decode(video=0)]
    assert actual == [round(timestamp) for timestamp in timestamps]


def test_failed_encoder_initialization_closes_output(tmp_path: Path) -> None:
    """An encoder setup error must not leave its already-open output handle alive."""
    container = MagicMock()
    container.add_stream.side_effect = RuntimeError("codec unavailable")
    with patch("ax_devil.modules.video_viewer.export.encoder.av.open", return_value=container):
        with pytest.raises(RuntimeError, match="codec unavailable"):
            VideoEncoder(tmp_path / "output.mp4", width=100, height=80, fps=25)
    container.close.assert_called_once_with()
