"""Video encoder — writes QImage frames to a video file via PyAV (ffmpeg)."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from pathlib import Path
from typing import cast

import av
import av.video
import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage


@dataclass(frozen=True, slots=True)
class CompressionPreset:
    """H.264 compression settings for export."""

    label: str
    crf: int
    preset: str

    @classmethod
    def lossless(cls) -> "CompressionPreset":
        """Visually lossless, large files."""
        return cls(label="Lossless (largest)", crf=0, preset="ultrafast")

    @classmethod
    def high_quality(cls) -> "CompressionPreset":
        """High quality, moderate size."""
        return cls(label="High quality", crf=18, preset="veryfast")

    @classmethod
    def balanced(cls) -> "CompressionPreset":
        """Good quality, reasonable size (recommended)."""
        return cls(label="Balanced (recommended)", crf=23, preset="veryfast")

    @classmethod
    def small_file(cls) -> "CompressionPreset":
        """Lower quality, smallest file size."""
        return cls(label="Smaller file", crf=28, preset="fast")

    @classmethod
    def all_presets(cls) -> list["CompressionPreset"]:
        """Return all presets in quality-descending order."""
        return [cls.lossless(), cls.high_quality(), cls.balanced(), cls.small_file()]

    @classmethod
    def default(cls) -> "CompressionPreset":
        """Return the recommended default preset."""
        return cls.balanced()


class VideoEncoder:
    """Encode a sequence of QImage frames into a video file."""

    def __init__(
        self,
        output_path: Path,
        *,
        width: int,
        height: int,
        fps: float,
        compression: CompressionPreset | None = None,
        codec: str = "libx264",
    ) -> None:
        if width % 2 != 0 or height % 2 != 0:
            raise ValueError(f"h.264 yuv420p requires even dimensions, got {width}x{height}")

        if not isfinite(fps) or fps <= 0:
            raise ValueError("Export requires a positive finite frame rate")
        self._time_base = Fraction(1, 1_000_000)
        self._first_timestamp_us: float | None = None
        self._last_pts: int | None = None
        self._frame_durations: dict[int, int] = {}
        self._default_duration_us = round(1_000_000 / fps)
        preset = compression or CompressionPreset.default()

        self._container = av.open(str(output_path), mode="w", format="mp4")
        try:
            stream = self._container.add_stream(codec, rate=Fraction(fps).limit_denominator(10000))
            self._stream = cast(av.video.VideoStream, stream)
            self._stream.time_base = self._time_base
            self._stream.codec_context.time_base = self._time_base
            # Keep decode order equal to presentation order so MP4 durations follow source timing.
            self._stream.codec_context.max_b_frames = 0
            self._stream.width = width
            self._stream.height = height
            self._stream.pix_fmt = "yuv420p"
            self._stream.options = {"crf": str(preset.crf), "preset": preset.preset}
        except BaseException:
            self._container.close()
            raise

    def write_frame(self, image: QImage, *, timestamp_us: float, duration_s: float | None = None) -> None:
        """Encode one frame at its source time, relative to the first exported frame."""
        if not isfinite(timestamp_us):
            raise ValueError("Cannot export a frame with an invalid timestamp")
        if self._first_timestamp_us is None:
            self._first_timestamp_us = timestamp_us
        pts = round(timestamp_us - self._first_timestamp_us)
        if self._last_pts is not None and pts <= self._last_pts:
            raise ValueError("Export frame timestamps must increase")
        duration_us = self._default_duration_us
        if duration_s is not None:
            if not isfinite(duration_s) or duration_s <= 0:
                raise ValueError("Cannot export a frame with an invalid duration")
            duration_us = round(duration_s * 1_000_000)
        arr = self._qimage_to_rgb(image)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = pts
        frame.time_base = self._time_base
        frame.duration = duration_us
        self._last_pts = pts
        self._frame_durations[pts] = duration_us
        for packet in self._stream.encode(frame):
            self._mux_packet(packet)

    def finish(self) -> None:
        """Flush the encoder and close the output file."""
        for packet in self._stream.encode():
            self._mux_packet(packet)
        self._container.close()

    def _mux_packet(self, packet: av.Packet[av.video.VideoStream]) -> None:
        # Encoders can reorder/delay packets and ignore frame.duration. Restore it by PTS.
        if packet.pts is None:
            raise RuntimeError("Encoded frame has no presentation timestamp")
        packet.duration = self._frame_durations.pop(packet.pts)
        self._container.mux(packet)

    def close(self) -> None:
        """Close the container without flushing. Use on cancellation."""
        self._container.close()

    @staticmethod
    def _qimage_to_rgb(image: QImage) -> NDArray[np.uint8]:
        """Convert a QImage to an RGB numpy array."""
        image = image.convertToFormat(QImage.Format.Format_RGB888)
        w = image.width()
        h = image.height()
        ptr = image.constBits()
        raw = np.frombuffer(ptr, dtype=np.uint8)
        arr = raw.reshape((h, image.bytesPerLine()))
        # bytesPerLine may include padding; slice to actual width*3
        result: NDArray[np.uint8] = arr[:, : w * 3].reshape((h, w, 3)).copy()
        return result
