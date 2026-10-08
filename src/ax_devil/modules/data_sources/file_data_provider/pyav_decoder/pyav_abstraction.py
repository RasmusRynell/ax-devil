"""PyAV-based video reading abstraction."""

from pathlib import Path
from typing import Any, Iterator, Optional

import av
from av.video.stream import VideoStream

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_index import FrameIndex
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import VideoOpenConfig
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

# Type alias for decoded frames
DecodedFrames = list[tuple[int, av.VideoFrame]]


class PyAvAbstraction:
    """PyAV-based video reader with reliable frame-accurate seeking.

    Provides sequential frame reading and precise frame-based navigation using
    a prebuilt frame index for consistent seeking. Automatically builds and caches
    frame index on first use for O(1) frame lookup.

    Features:
    - Frame-accurate seeking via PTS-based indexing
    - Automatic threading optimization for 5x faster decoding
    - Context manager support for resource cleanup
    - Caches decoded frames during jump operations

    Note: Not thread-safe - use separate instances for concurrent access.
    """

    def __init__(
        self,
        video_path: str,
        cache_path: Optional[Path] = None,
        open_config: VideoOpenConfig | None = None,
    ) -> None:
        """Initialize video reader with frame indexing.

        Args:
            video_path: Path to video file to read.
            cache_path: Optional path for frame index cache file.
            open_config: Optional :class:`VideoOpenConfig` with demuxer hints
                (e.g. ``image2`` format for image sequences).

        Raises:
            ValueError: If no video streams found in file.
            FileNotFoundError: If video file doesn't exist.

        Note: On first use, builds frame index by scanning entire video.
        If cache_path is provided, index is cached there for subsequent loads.
        """
        self.video_path = Path(video_path)
        self._open_config = open_config

        av_format = open_config.av_format if open_config else None
        av_options = open_config.av_options if open_config else None
        self.container = av.open(video_path, format=av_format, options=av_options)

        if not self.container.streams.video:
            raise ValueError(f"No video streams found in {video_path}")

        self.stream: VideoStream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"  # 5x faster decoding
        self._current_frame_index = 0
        self._packet_gen: Optional[Iterator[av.Packet[VideoStream]]] = None
        self._frame_gen: Optional[Iterator[av.VideoFrame]] = None

        # Initialize frame index for reliable seeking
        self.frame_index = FrameIndex(self.video_path, cache_path, open_config=open_config)
        if not self.frame_index.load_from_file():
            logger.debug(f"Building frame index for {self.video_path.name} (this may take a moment...)")
            self.frame_index.build()
            self.frame_index.save_to_file()

        logger.debug(f"PyAvAbstraction created: id={id(self)}, video={self.video_path.name}")

    @property
    def current_frame_index(self) -> int:
        """Current frame index position."""
        return self._current_frame_index

    # Public API methods
    def read_next(self) -> Optional[av.VideoFrame]:
        """Read and decode the next frame sequentially.

        Returns:
            av.VideoFrame: Raw PyAV VideoFrame object, or None if end of video stream reached.

        Note: Automatically skips non-video frames (e.g., subtitle frames).
        Call repeatedly to iterate through all frames in order.
        """
        frame = self._get_next_frame()
        if frame is None:
            return None

        # Ensure we only process video frames, not subtitle frames
        if not hasattr(frame, "to_ndarray"):
            logger.debug(f"Skipping non-video frame at index {self._current_frame_index}")
            return self.read_next()  # Recursively try next frame

        self._current_frame_index += 1
        return frame

    def read_until(self, target_frame_index: int) -> DecodedFrames:
        """Read frames sequentially until reaching target frame index.

        Calls read_next() repeatedly to collect all frames from current position
        up to and including the target frame. More efficient than jump_to() when
        reading a contiguous sequence of frames.

        Args:
            target_frame_index: Frame index to read until (inclusive, 0-based)

        Returns:
            DecodedFrames: List of (frame_index, av.VideoFrame) tuples for all frames
            read from current position to target. Empty list if target is behind current
            position or out of bounds.

        Note: Updates internal position to frame after target for subsequent operations.
        For random access patterns, consider using jump_to() instead.
        """
        if not (0 <= target_frame_index < self.frame_index.get_total_frames()):
            raise ValueError(
                f"Target frame index {target_frame_index} out of bounds (0-{self.frame_index.get_total_frames() - 1})"
            )

        if self._current_frame_index > target_frame_index:
            raise ValueError(
                f"Current position {self._current_frame_index} is past target {target_frame_index} "
                "- cannot read backwards"
            )

        decoded_frames: DecodedFrames = []

        while self._current_frame_index <= target_frame_index:
            frame_index_before_read = self._current_frame_index
            frame = self.read_next()

            if frame is None:
                logger.debug(f"Reached end of stream at frame {self._current_frame_index - 1}")
                break

            decoded_frames.append((frame_index_before_read, frame))

            if frame_index_before_read == target_frame_index:
                break

        logger.debug(
            f"Read {len(decoded_frames)} frames from "
            f"{decoded_frames[0][0] if decoded_frames else 'N/A'} to {target_frame_index}"
        )
        return decoded_frames

    def jump_to(self, frame_index: int) -> DecodedFrames:
        """Jump to specific frame with frame-accurate seeking.

        Uses prebuilt frame index to seek to nearest keyframe, then decodes forward
        to target frame. Returns all intermediate frames for efficient caching.

        Args:
            frame_index: Target frame number (0-based indexing)

        Returns:
            DecodedFrames: List of (frame_index, av.VideoFrame) tuples for all frames
            decoded from keyframe to target. Empty list if frame_index is out of bounds.

        Note: Updates internal position to frame after target for subsequent read_next() calls.
        More efficient than multiple read_next() calls for random access patterns.
        """
        logger.debug(f"Jumping from frame {self._current_frame_index} to frame {frame_index}")

        if not (0 <= frame_index < self.frame_index.get_total_frames()):
            raise ValueError(f"Frame index {frame_index} out of bounds (0-{self.frame_index.get_total_frames() - 1})")

        if frame_index == 0:
            if self._current_frame_index != 0:
                self._reset_to_stream_start()
            return self.read_until(0)

        # Reset iterators
        self._packet_gen = None
        self._frame_gen = None

        # Get exact PTS for target frame from index
        target_pts = self.frame_index.get_frame_pts(frame_index)

        # Find nearest keyframe before target frame
        keyframe_idx = self.frame_index.get_nearest_keyframe_before(frame_index)
        keyframe_pts = self.frame_index.get_frame_pts(keyframe_idx)

        logger.debug(
            f"Seeking to keyframe {keyframe_idx} (PTS {keyframe_pts}) "
            f"then forward to frame {frame_index} (PTS {target_pts})"
        )

        # Seek to keyframe
        self.container.seek(keyframe_pts, stream=self.stream, any_frame=False, backward=True)

        # Decode frames from keyframe to target
        return self._decode_frames_to_target(keyframe_idx, target_pts, frame_index)

    def get_total_frames(self) -> int:
        """Get total frame count from prebuilt frame index.

        Returns:
            int: Total number of frames in the video.
        """
        total_frames: int = self.frame_index.get_total_frames()
        return total_frames

    def get_frame_time_us(self, frame_index: int) -> float:
        """Get frame presentation time in microseconds since the first indexed frame."""
        return self.frame_index.get_frame_time_us(frame_index)

    def get_frame_times_us(self) -> tuple[int, ...]:
        """Get all frame presentation times in microseconds since the first indexed frame."""
        return self.frame_index.get_frame_times_us()

    def get_frame_period_after_s(self, frame_index: int) -> float | None:
        """Get presentation delay from this frame to the next frame."""
        return self.frame_index.get_frame_period_after_s(frame_index)

    def get_frame_source_timing_metadata(self, frame_index: int) -> dict[str, Any]:
        """Return raw source timing facts for debug display."""
        return self.frame_index.get_frame_source_timing_metadata(frame_index)

    def close(self) -> None:
        """Release video container resources.

        Safe to call multiple times. Automatically called by context manager.
        """
        self._safe_close_resource("container", "PyAV container")

    def __enter__(self) -> "PyAvAbstraction":
        """Context manager entry - returns self."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - ensures resource cleanup."""
        self.close()

    # Internal helper methods

    def _get_next_frame(self) -> Optional[av.VideoFrame]:
        """Get the next decoded frame from current stream position."""
        self._ensure_packet_generator()

        while True:
            frame = self._try_get_frame_from_current_packet()
            if frame is not None:
                return frame

            if not self._advance_to_next_packet():
                return None  # End of stream

    def _ensure_packet_generator(self) -> None:
        """Initialize packet generator if not already created."""
        if self._packet_gen is None:
            self._packet_gen = self.container.demux(self.stream)

    def _try_get_frame_from_current_packet(self) -> Optional[av.VideoFrame]:
        """Extract next frame from current packet's frame generator."""
        if self._frame_gen is None:
            return None

        try:
            return next(self._frame_gen)
        except StopIteration:
            self._frame_gen = None
            return None

    def _advance_to_next_packet(self) -> bool:
        """Advance to the next packet and prepare frame generator. Returns False if end of stream."""
        if self._packet_gen is None:
            return False

        try:
            packet = next(self._packet_gen)
            frames = packet.decode()
            if frames:
                self._frame_gen = iter(frames)
                return True
            return True  # No frames in this packet, caller will continue loop
        except StopIteration:
            return False  # End of stream

    def _decode_frames_to_target(self, keyframe_idx: int, target_pts: int, target_frame_index: int) -> DecodedFrames:
        """Decode all frames from keyframe position to target frame index.

        After ``container.seek()`` the actual start position may differ from
        ``keyframe_idx`` (e.g. ASF containers often land earlier).  We therefore
        resolve each decoded frame's real index via its PTS instead of assuming
        the first frame is at ``keyframe_idx``.
        """
        decoded_frames: DecodedFrames = []
        self._packet_gen = self.container.demux(self.stream)
        pts_to_index = self.frame_index.pts_to_index

        target_found = False
        last_real_idx = keyframe_idx
        if self._packet_gen is None:
            logger.warning("Packet generator is None during frame decoding")
            return decoded_frames

        for packet in self._packet_gen:
            frames = list(packet.decode())
            for frame in frames:
                if frame.pts is None:
                    continue

                # Ensure we only process video frames, not subtitle frames
                if not hasattr(frame, "to_ndarray"):
                    logger.debug(f"Skipping non-video frame with pts={frame.pts}")
                    continue

                # Resolve real frame index from PTS
                real_idx = pts_to_index.get(frame.pts)
                if real_idx is None:
                    logger.debug(f"Decoded frame with unknown PTS {frame.pts}, skipping")
                    continue

                # Skip frames before the keyframe we actually wanted
                if real_idx < keyframe_idx:
                    continue

                decoded_frames.append((real_idx, frame))
                last_real_idx = real_idx

                if real_idx == target_frame_index:
                    target_found = True

            if target_found:
                self._update_position_after_jump(last_real_idx + 1)
                logger.debug(f"Successfully decoded {len(decoded_frames)} frames (up to frame {last_real_idx})")
                return decoded_frames

        logger.warning(f"Could not find target frame {target_frame_index} with PTS {target_pts}")
        return decoded_frames

    def _update_position_after_jump(self, next_frame_index: int) -> None:
        """Reset internal position tracking after jump operation."""
        self._current_frame_index = next_frame_index
        self._frame_gen = None  # Reset frame generator

    def _reset_to_stream_start(self) -> None:
        """Reset the decoder without seeking so demuxers can return the first frame."""
        self._safe_close_resource("container", "PyAV container")
        av_format = self._open_config.av_format if self._open_config else None
        av_options = self._open_config.av_options if self._open_config else None
        self.container = av.open(str(self.video_path), format=av_format, options=av_options)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        self._current_frame_index = 0
        self._packet_gen = None
        self._frame_gen = None

    def _safe_close_resource(self, attr_name: str, resource_name: str) -> None:
        """Close resource with exception handling and logging."""
        try:
            resource = getattr(self, attr_name, None)
            if resource and hasattr(resource, "close"):
                resource.close()
        except Exception as e:
            logger.warning(f"Error closing {resource_name}: {e}")
