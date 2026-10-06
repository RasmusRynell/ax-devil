"""Seekable file-video source backed by one serialized frame-delivery worker."""

from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ax_devil.core.data_types import FrameData, FrameIdentifier
from ax_devil.modules.cache import CacheManager
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder import ImageSequenceConfig
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, VideoTimingProfile
from ax_devil.modules.data_sources.video_analyzer import VideoAnalyzer
from ax_devil.modules.data_sources.video_cache_memory import get_video_cache_pool
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.engine.playback_speed import clamp_playback_speed

from .base import SeekableFrameSource
from .file_data_provider.pyav_decoder.video_open_config import VideoOpenConfig
from .file_frame_delivery import FileFrameDelivery

# Global configuration for video metadata analysis
VIDEO_CACHE_SUFFIX = ".meta.json"
FFPROBE_PATH = "ffprobe"
FFMPEG_PATH = "ffmpeg"
VIDEO_DEEP_SCAN_ARGS = ("-probesize", "500M", "-analyzeduration", "500M")

logger = get_logger(__name__)


class FileFrameSource(SeekableFrameSource):
    """Qt source facade for one seekable file-video delivery runtime."""

    sourceFinished = Signal()  # Signal emitted when paced playback reaches EOF

    def __init__(
        self,
        file_path: str,
        source_id: str = "file_video",
        parent: QObject | None = None,
        image_sequence_config: ImageSequenceConfig | None = None,
    ):
        super().__init__(parent=parent)

        logger.debug(f"Initializing FileFrameSource for: {file_path}")

        self.config_manager = ConfigManager()
        self.file_path = file_path
        self.source_id = source_id
        self._frame_delivery: FileFrameDelivery | None = None
        self.fps: float | int = -1
        self.total_frames = -1
        self._original_size = (-1, -1)
        self._image_sequence_config = image_sequence_config
        self._timing_profile: VideoTimingProfile | None = None
        self._frame_timestamps_us: tuple[int, ...] | None = None
        self._frame_timeline: FrameTimeline | None = None

        # Only needed for regular video files; image sequences carry their own metadata
        self._video_analyzer: VideoAnalyzer | None = None

        # Open video to get dimensions
        self._open_video()

    def _on_delivery_finished(self) -> None:
        """Forward an end-of-file event from the source delivery worker."""
        logger.debug(f"Frame delivery finished for {self.source_id}")
        self.sourceFinished.emit()

    def _open_video(self) -> None:
        """Open video file and extract basic info using PyAV and dynamic prefetching cache."""
        logger.debug(f"Opening video file with PyAV: {self.file_path}")

        isc = self._image_sequence_config
        open_config: VideoOpenConfig | None = None

        if isc is not None:
            open_config = isc.to_video_open_config()
            logger.debug(
                f"Image sequence mode: fps={isc.fps}, size={isc.width}x{isc.height}, "
                f"frames={isc.total_frames}, start={isc.start_number}"
            )

        try:
            cache_manager = CacheManager()
            cache_path = cache_manager.get_cache_path(
                cache_type="frame_index", source_path=Path(self.file_path), suffix=".ptsidx"
            )
            self._frame_delivery = FileFrameDelivery.create(
                video_path=self.file_path,
                fps=1.0,
                source_id=self.source_id,
                cache_pool=get_video_cache_pool(),
                prefetch_count=50,
                cache_path=cache_path,
                open_config=open_config,
                on_playback_frame=self.emit_decoded_frame,
                on_playback_finished=self._on_delivery_finished,
            )
            self._frame_delivery.open()

            if isc is not None:
                self._apply_image_sequence_metadata(isc)
            else:
                self._probe_video_metadata()

            self._frame_delivery.set_fps(float(self.fps))

        except Exception as e:
            import traceback

            if self._frame_delivery is not None:
                self._frame_delivery.close()
                self._frame_delivery = None
            logger.debug(traceback.format_exc())
            logger.error(f"Error opening video with PyAV: {e}")
            raise ValueError(f"Error opening video with PyAV: {e}")

        if self.total_frames <= 0:
            logger.error(f"Invalid total frames: {self.total_frames} for video {self.file_path}")
            self.stop()
            raise ValueError(f"Invalid total frames: {self.total_frames} for video {self.file_path}")
        if self.fps <= 0:
            logger.error(f"Invalid FPS: {self.fps} for video {self.file_path}")
            self.stop()
            raise ValueError(f"Invalid FPS: {self.fps} for video {self.file_path}")

        w, h = self._original_size
        logger.debug(f"Video opened successfully with PyAV: {w}x{h} @ {self.fps} FPS, {self.total_frames} frames")

    def _apply_image_sequence_metadata(self, isc: ImageSequenceConfig) -> None:
        """Apply pre-known metadata from an :class:`ImageSequenceConfig`."""
        self._original_size = (isc.width, isc.height)
        self.fps = isc.fps
        self.total_frames = isc.total_frames

    def _probe_video_metadata(self) -> None:
        """Extract metadata from a regular video file via ``VideoAnalyzer``."""
        if self._video_analyzer is None:
            self._video_analyzer = VideoAnalyzer(
                ffprobe=FFPROBE_PATH,
                ffmpeg=FFMPEG_PATH,
                cache_suffix=VIDEO_CACHE_SUFFIX,
                deep_args=VIDEO_DEEP_SCAN_ARGS,
            )

        metadata = self._video_analyzer.analyze(Path(self.file_path))
        width_raw = metadata.get("width")
        height_raw = metadata.get("height")
        fps_raw = metadata.get("fps")
        width = int(width_raw) if width_raw is not None else -1
        height = int(height_raw) if height_raw is not None else -1
        self._original_size = (width, height)
        self.fps = float(fps_raw) if fps_raw is not None else -1.0

        if self._frame_delivery is None:
            raise RuntimeError("Frame delivery is not available after opening video")
        self.total_frames = self._frame_delivery.get_total_frames()

    def play(self) -> bool:
        """Start paced frame delivery."""
        logger.debug(f"Play requested for {self.source_id}")
        return self._frame_delivery.play() if self._frame_delivery is not None else False

    def pause(self) -> None:
        """Pause frame production."""
        logger.debug(f"Pause requested for {self.source_id}")
        if self._frame_delivery is not None:
            self._frame_delivery.pause()

    def stop(self) -> None:
        """Stop frame delivery and release its decoder."""
        logger.debug(f"Stopping {self.source_id} (begin)")
        if self._frame_delivery is not None:
            self._frame_delivery.close()
            self._frame_delivery = None

        self.cleanup_posted_events()
        logger.debug(f"Stopped {self.source_id} (done)")

    def wait(self, timeout: int = 2000) -> bool:
        """Wait for delivery shutdown."""
        return self._frame_delivery.wait(timeout) if self._frame_delivery is not None else True

    def jump_to(self, frame_number: int) -> None:
        """Jump to specific frame number and read that frame."""
        logger.debug(f"Jumping to frame {frame_number}")

        if frame_number < 0 or frame_number > self.total_frames - 1:
            logger.debug(f"Frame number {frame_number} out of bounds (0 to {self.total_frames - 1})")
            return

        if self._frame_delivery is not None:
            self._frame_delivery.jump_to(frame_number)

    def request_frame_async(self, frame_number: int, callback: Callable[[FrameData | None], None]) -> None:
        """Request a frame asynchronously without decoding on the caller thread."""
        if frame_number < 0 or frame_number > self.total_frames - 1:
            logger.warning(f"Frame number {frame_number} out of bounds (0 to {self.total_frames - 1})")
            callback(None)
            return
        if self._frame_delivery is None:
            callback(None)
            return

        def on_frame(decoded_frame: DecodedFrame | None) -> None:
            if decoded_frame is None:
                callback(None)
                return
            try:
                callback(self._build_frame_data(decoded_frame))
            except Exception as exc:
                logger.error(f"Error building async frame {frame_number}: {exc}")
                callback(None)

        self._frame_delivery.request_frame_async(frame_number, on_frame)

    def read_decoded_frame(self, frame_number: int) -> DecodedFrame | None:
        """Read one decoded frame for a non-playback consumer through source delivery."""
        if frame_number < 0 or frame_number > self.total_frames - 1:
            logger.warning(f"Frame number {frame_number} out of bounds (0 to {self.total_frames - 1})")
            return None
        if self._frame_delivery is None:
            return None
        return self._frame_delivery.read_decoded_frame(frame_number)

    def get_total_frames(self) -> int:
        """Get total number of frames in the video."""
        return self.total_frames

    def get_current_frame(self) -> int:
        """Get current frame position."""
        return self._frame_delivery.get_current_frame() if self._frame_delivery is not None else -1

    def reset_to_start(self) -> None:
        """Reset video to start position."""
        logger.debug("Resetting video to start position")

        if self._frame_delivery is not None:
            self._frame_delivery.jump_to(0)

    def set_playback_speed(self, speed: float) -> None:
        """Set playback speed multiplier for frame pacing."""
        if self._frame_delivery is not None:
            self._frame_delivery.set_playback_speed(clamp_playback_speed(speed))

    def get_playback_speed(self) -> float:
        """Return current playback speed multiplier."""
        return self._frame_delivery.get_playback_speed() if self._frame_delivery is not None else 1.0

    def get_position_generation(self) -> int:
        """Return the current explicit-position generation."""
        return self._frame_delivery.get_position_generation() if self._frame_delivery is not None else 0

    def _build_frame_data(
        self,
        decoded_frame: DecodedFrame,
        position_generation: int | None = None,
    ) -> FrameData:
        """Convert a decoded reader frame into ``FrameData``."""
        frame_rgb = decoded_frame.pixels
        height, width, _ = frame_rgb.shape
        bytes_per_line = 3 * width
        frame_rgb = np.ascontiguousarray(frame_rgb)
        qimage = QImage(frame_rgb.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
        frame_id = FrameIdentifier(
            sequence_id=decoded_frame.frame_index,
            timestamp_monotime_us=decoded_frame.timestamp_us,
        )
        metadata = dict(decoded_frame.source_timing_metadata)
        if decoded_frame.period_after_s is not None:
            metadata["video_period_after_s"] = decoded_frame.period_after_s
            metadata["video_period_source"] = "pts_delta"
        if position_generation is not None:
            metadata["position_generation"] = position_generation
        return FrameData(content=qimage, frame_id=frame_id, source_id=self.source_id, metadata=metadata or None)

    def emit_decoded_frame(
        self,
        decoded_frame: DecodedFrame,
        position_generation: int | None = None,
    ) -> None:
        """Emit a decoded reader frame through the standard ``frameReady`` signal."""
        self.frameReady.emit(self._build_frame_data(decoded_frame, position_generation))

    def get_frame_period_after_s(self, frame_number: int) -> float:
        """Return playback delay from a frame to the next frame."""
        if self._frame_delivery is not None:
            try:
                period = self._frame_delivery.get_frame_period_after_s(frame_number)
            except (IndexError, RuntimeError, ValueError):
                period = None
            if period is not None and period > 0.0:
                return period

        return 1.0 / float(self.fps)

    def get_timing_profile(self) -> VideoTimingProfile | None:
        """Return decoded video timing diagnostics, if available."""
        if self._timing_profile is not None:
            return self._timing_profile
        if self._frame_delivery is None:
            return None
        try:
            self._timing_profile = self._frame_delivery.analyze_timing_profile()
        except (IndexError, RuntimeError, ValueError) as exc:
            logger.debug(f"Failed to analyze timing profile for {self.source_id}: {exc}")
            return None
        return self._timing_profile

    def get_frame_timestamps_us(self) -> tuple[int, ...]:
        """Return decoded video frame timestamps in microseconds."""
        if self._frame_timestamps_us is not None:
            return self._frame_timestamps_us
        if self._frame_delivery is None:
            return ()
        try:
            timestamps = self._frame_delivery.get_frame_times_us()
        except (IndexError, RuntimeError, ValueError) as exc:
            logger.debug(f"Failed to read frame timestamps for {self.source_id}: {exc}")
            return ()
        if len(timestamps) != self.total_frames:
            logger.debug(
                f"Read {len(timestamps)} timestamps for {self.source_id}, expected {self.total_frames}; "
                "leaving frame timestamp cache empty"
            )
            return timestamps
        self._frame_timestamps_us = timestamps
        return self._frame_timestamps_us

    def get_frame_timeline(self) -> FrameTimeline:
        """Return source-owned frame timeline diagnostics."""
        if self._frame_timeline is None:
            self._frame_timeline = FrameTimeline.lazy(
                count=self.total_frames,
                timestamp_loader=self.get_frame_timestamps_us,
            )
        return self._frame_timeline
