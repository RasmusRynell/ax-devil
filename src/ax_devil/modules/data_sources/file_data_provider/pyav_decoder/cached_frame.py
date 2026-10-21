"""Cached decoded frame wrapper for lazy numpy array conversion."""

from typing import Any

import av
import numpy as np
from numpy.typing import NDArray

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.settings.logging_config import get_logger

_SWSCALE_RGBA_ROW_MULTIPLE = 8


class CachedFrame:
    """Wrapper for decoded frames that performs lazy pixel conversion.

    Stores source timing facts with the source frame and converts to RGBA pixels
    on each delivery. Frames that are cached but never delivered are never
    converted, and converted pixels are not retained by the cache.

    Note: Assumes input VideoFrame is valid and has to_ndarray method.
    """

    def __init__(
        self,
        *,
        frame_index: int,
        video_frame: av.VideoFrame,
        timestamp_us: int,
        period_after_s: float | None,
        source_timing_metadata: dict[str, Any],
    ) -> None:
        """Initialize with a validated frame object and source timing facts.

        Args:
            frame_index: Zero-based decoded presentation index.
            video_frame: Valid PyAV VideoFrame object with to_ndarray method
            timestamp_us: Frame timestamp in microseconds from the start of the source.
            period_after_s: Presentation period from this frame to the next, if known.
            source_timing_metadata: Raw source timing facts for debug display.
        """
        self.logger = get_logger(__name__)
        self.frame_index = frame_index
        self.timestamp_us = timestamp_us
        self.period_after_s = period_after_s
        self.duration_s = (
            float(video_frame.duration * video_frame.time_base)
            if video_frame.duration > 0 and video_frame.time_base is not None
            else None
        )
        self.source_timing_metadata = source_timing_metadata
        # Only the source frame is retained; each delivery converts into a fresh array.
        self._reserved_bytes = sum(plane.buffer_size for plane in video_frame.planes)
        self._video_frame = video_frame
        self.logger.debug(
            f"CachedFrame created: id={id(self)}, frame_index={frame_index}, has_frame={video_frame is not None}"
        )

    def to_decoded_frame(self) -> DecodedFrame:
        """Return the complete decoded frame payload, converting pixels.

        Returns:
            DecodedFrame: RGBA pixels (alpha 255) plus source timing facts.
        """
        return DecodedFrame(
            frame_index=self.frame_index,
            pixels=self._to_numpy(),
            timestamp_us=self.timestamp_us,
            period_after_s=self.period_after_s,
            source_timing_metadata=self.source_timing_metadata,
            duration_s=self.duration_s,
        )

    def _to_numpy(self) -> NDArray[Any]:
        self.logger.debug("Converting VideoFrame to numpy array")
        frame = self._video_frame
        # threads=1 skips swscale's thread pool, which costs more CPU than the conversion itself at video sizes.
        # The array views the converted frame's plane and keeps it alive, so it outlives this cache entry.
        if frame.width % _SWSCALE_RGBA_ROW_MULTIPLE == 0:
            return frame.to_ndarray(format="rgba", threads=1)
        # swscale leaves the last 2 or 4 pixels of each row unwritten in 32-bit RGB output for some other widths.
        rgb = frame.to_ndarray(format="rgb24", threads=1)
        rgba = np.full((*rgb.shape[:2], 4), 255, dtype=np.uint8)
        rgba[..., :3] = rgb
        return rgba

    @property
    def reserved_bytes(self) -> int:
        """Source plane bytes retained for this entry's lifetime."""
        return self._reserved_bytes

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"CachedFrame(frame_index={self.frame_index})"
