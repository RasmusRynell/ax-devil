"""Cached decoded frame wrapper for lazy numpy array conversion."""

from typing import Any

import av
import numpy as np

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.settings.logging_config import get_logger


class CachedFrame:
    """Wrapper for decoded frames that performs lazy pixel conversion.

    Stores source timing facts with the frame and converts to RGB pixels only on
    first access. This avoids unnecessary conversion work for frames that are
    cached but never used while keeping decoded-frame metadata intact.

    Note: Assumes input VideoFrame is valid and has to_ndarray method.
    """

    def __init__(
        self,
        *,
        frame_index: int,
        video_frame: av.VideoFrame,
        timestamp_us: float,
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
        # Reserve for the larger retained representation, including source-plane padding.
        # RGB conversion can then happen without mutating cache accounting.
        self._reserved_bytes = max(
            sum(plane.buffer_size for plane in video_frame.planes), video_frame.width * video_frame.height * 3
        )
        self._video_frame: av.VideoFrame | None = video_frame
        self._numpy_array: np.ndarray | None = None
        self._is_converted = False
        self.logger.debug(
            f"CachedFrame created: id={id(self)}, frame_index={frame_index}, has_frame={video_frame is not None}"
        )

    def __del__(self) -> None:
        """Destructor with debug logging."""
        try:
            has_video_frame = hasattr(self, "_video_frame") and self._video_frame is not None
            has_numpy_array = hasattr(self, "_numpy_array") and self._numpy_array is not None
            is_converted = getattr(self, "_is_converted", False)
            self.logger.debug(
                f"CachedFrame destroyed: id={id(self)}, converted={is_converted}, "
                f"has_video_frame={has_video_frame}, has_numpy_array={has_numpy_array}"
            )
        except Exception:
            pass

    def to_decoded_frame(self) -> DecodedFrame:
        """Return the complete decoded frame payload, converting pixels on first access.

        Returns:
            DecodedFrame: RGB24 pixels plus source timing facts.
        """
        return DecodedFrame(
            frame_index=self.frame_index,
            pixels=self._to_numpy(),
            timestamp_us=self.timestamp_us,
            period_after_s=self.period_after_s,
            source_timing_metadata=self.source_timing_metadata,
            duration_s=self.duration_s,
        )

    def _to_numpy(self) -> np.ndarray:
        if self._is_converted:
            assert self._numpy_array is not None, "Converted frame should have numpy array"
            return self._numpy_array
        # Perform the conversion
        assert self._video_frame is not None, "Video frame should be available for conversion"
        self.logger.debug("Converting VideoFrame to numpy array")
        self._numpy_array = self._video_frame.to_ndarray(format="rgb24")
        self._is_converted = True

        # Clear reference to VideoFrame to free memory
        self._video_frame = None

        return self._numpy_array

    @property
    def reserved_bytes(self) -> int:
        """Reserve the larger of source planes and RGB pixels for this entry's lifetime."""
        return self._reserved_bytes

    @property
    def is_converted(self) -> bool:
        """Check if frame has been converted to numpy array."""
        return self._is_converted

    def __repr__(self) -> str:
        """String representation for debugging."""
        status = "converted" if self._is_converted else "raw"
        return f"CachedFrame(frame_index={self.frame_index}, {status})"
