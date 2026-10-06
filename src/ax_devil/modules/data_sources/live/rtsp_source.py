"""RTSP video transport with optional embedded Scene decoding."""

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import ERROR
from threading import Lock
from typing import Any, Deque, Optional, cast

import numpy as np
from ax_devil_rtsp.rtsp_data_retrievers import RtspDataRetriever
from numpy.typing import NDArray
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.filtering import FilterConfig, FilterFactory, build_default_filter_config
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.settings.logging_config import get_logger

from ..base import FrameSource, OverlaySource, Worker

logger = get_logger(__name__)


class _RTSPWorker(Worker):
    """Worker that handles RTSP frame and overlay processing."""

    def __init__(self, rtsp_source: "RTSPSource", source_id: str) -> None:
        super().__init__(source_id)
        self.rtsp_source = rtsp_source

    def run_loop(self) -> bool:
        """Process frames and overlays from RTSP buffers."""
        return self.rtsp_source._process_data()


@dataclass(frozen=True)
class RTSPOverlayDecoder:
    """Decoder and filter definition for enabled embedded RTSP overlays."""

    decoder: PayloadToSceneDecoder
    handler_type: str
    filter_factory: FilterFactory | None = None


class RTSPSource(FrameSource, OverlaySource):
    """One RTSP transport providing video and optionally decoded embedded overlays."""

    frameReady = Signal(FrameData)
    overlayReady = Signal(OverlayData)
    sourceError = Signal(str)

    def __init__(
        self,
        rtsp_url: str,
        source_id: str = "rtsp",
        buffer_size: int = 10,
        parent: Optional[QObject] = None,
        *,
        overlay: RTSPOverlayDecoder | None = None,
    ) -> None:
        super().__init__(parent=parent)

        logger.info(f"Initializing RTSP source {source_id}")

        self.source_id = source_id
        self.rtsp_url = rtsp_url
        self._overlay = overlay
        self.frame_buffer: Deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self.overlay_buffer: Deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self.buffer_lock = Lock()
        self.frame_count = 0
        self.overlay_count = 0
        self.retriever_started = False
        self.has_warned_frame_count_mismatch = False
        self.connection_timeout = 10  # seconds

        self._worker = _RTSPWorker(self, source_id)
        self._own_worker(self._worker)
        self._worker.sourceError.connect(self.sourceError.emit)
        self._worker.sourceFinished.connect(self._on_worker_finished)

        self.retriever: RtspDataRetriever | None = RtspDataRetriever(
            rtsp_url=self.rtsp_url,
            on_video_data=self._on_video_frame,
            on_application_data=self._on_application_data if overlay is not None else None,
            on_error=self._on_error,
            on_session_start=self._on_session_start,
            latency=100,  # Low latency for real-time
            connection_timeout=self.connection_timeout,
            log_level=ERROR,
        )

    def _on_worker_finished(self) -> None:
        """Handle worker finished signal."""
        logger.debug(f"Worker finished for {self.source_id}")

    def _on_session_start(self, payload: dict[str, Any]) -> None:
        """Callback when session starts."""
        logger.debug(f"RTSP session started for {self.source_id}")
        self.sourceConnected.emit()

    def _on_video_frame(self, payload: dict[str, Any]) -> None:
        """Callback when video frame is received from RTSP."""
        try:
            # Store frame in buffer
            with self.buffer_lock:
                self.frame_buffer.append(payload)

        except Exception as e:
            logger.error(f"Error in RTSP video frame callback: {e}")

    def _on_application_data(self, payload: dict[str, Any]) -> None:
        """Callback when application data is received from RTSP."""
        try:
            # Store application data in overlay buffer
            with self.buffer_lock:
                self.overlay_buffer.append(payload)

        except Exception as e:
            logger.error(f"Error in RTSP application data callback: {e}")

    def _on_error(self, payload: dict[str, Any]) -> None:
        """Handle RTSP errors."""
        error_msg = payload.get("message", "Unknown RTSP error")
        logger.error(f"RTSP Error: {error_msg}")
        self.sourceError.emit(f"RTSP Error: {error_msg}")

    def play(self) -> bool:
        """Start RTSP stream and data processing."""
        logger.debug(f"Play requested for {self.source_id}")

        if self.retriever is None:
            return False  # Stopped sources are terminal; reopening creates a new source.

        # Start RTSP retriever if not already running
        if not self.retriever_started:
            try:
                logger.debug("Starting RTSP retriever")
                self.retriever.start()
                self.retriever_started = True
                logger.debug("RTSP retriever started successfully")
            except Exception as e:
                logger.error(f"Failed to start RTSP: {str(e)}")
                self.sourceError.emit(f"Failed to start RTSP: {str(e)}")
                return False
        elif self.retriever_started:
            logger.debug("RTSP retriever already started, skipping start")

        # Start worker for data processing
        result = self._worker.play()
        logger.debug(f"Play result: {result}, worker running: {self._worker.is_playing()}")
        return result

    def pause(self) -> None:
        """Pause data processing."""
        logger.debug(f"Pause requested for {self.source_id}")
        self._worker.pause()

    def stop(self) -> None:
        """Stop RTSP stream and clean up."""
        logger.debug(f"Stopping {self.source_id}")

        # Request worker shutdown and allow up to two seconds for processing to finish.
        self._worker.stop()
        if not self._worker.wait(2000):
            logger.warning(f"Worker thread did not exit within 2s for {self.source_id}")

        # Stop RTSP retriever
        if self.retriever:
            try:
                self.retriever.stop()
                logger.debug("RTSP retriever stopped")
            except Exception as e:
                logger.warning(f"Error stopping RTSP retriever: {e}")
            self.retriever = None

        # Discard buffered input after stopping the transport.
        with self.buffer_lock:
            self.frame_buffer.clear()
            self.overlay_buffer.clear()
        self.frame_count = 0
        self.overlay_count = 0
        self.retriever_started = False

        self.cleanup_posted_events()
        logger.debug(f"Stopped {self.source_id}")

    def wait(self, timeout: int = 2000) -> bool:
        """Wait for worker to finish."""
        return bool(self._worker.wait(timeout))

    def _process_data(self) -> bool:
        """Process both video frames and application data.

        Returns True to keep the run loop alive. The Worker.run() loop handles
        pause/stop — this method never decides thread lifecycle.
        """
        frame_processed = self._process_video_frame()
        overlay_processed = self._process_application_data()

        if not frame_processed and not overlay_processed:
            time.sleep(0.01)  # Avoid busy-spin when no data available

        return True

    def _process_video_frame(self) -> bool:
        """Process video frame from buffer. Returns True if a frame was processed."""
        with self.buffer_lock:
            if len(self.frame_buffer) == 0:
                return False
            frame = self.frame_buffer.popleft()

        frame_data: NDArray[np.uint8] = cast(NDArray[np.uint8], frame.get("data"))
        latest_rtp = frame.get("latest_rtp_data") or {}
        capture_timestamp_str = latest_rtp.get("human_time", "")

        if not capture_timestamp_str or capture_timestamp_str == "":
            logger.warning("No capture time found! data cannot be used with sync, not yet implemented!")
            return False

        # Convert from "2025-07-12 16:10:01.033397 UTC" to float (epoch, including milliseconds)
        dt = datetime.strptime(capture_timestamp_str, "%Y-%m-%d %H:%M:%S.%f UTC").replace(tzinfo=timezone.utc)
        capture_timestamp = dt.timestamp()

        diagnostics = frame.get("diagnostics") or {}
        stream_frame_count = diagnostics.get("video_sample_count")

        # Convert to QImage - ensure data is contiguous and properly copied
        height, width = frame_data.shape[:2]
        bytes_per_line = 3 * width

        # Ensure the array is contiguous and make a proper copy
        frame_array = np.ascontiguousarray(frame_data).tobytes()
        qimage = QImage(frame_array, width, height, bytes_per_line, QImage.Format.Format_RGB888).copy()

        # Emit frame
        self.frame_count += 1
        # For RTSP, use device-provided capture timestamp as monotime
        frame_id = FrameIdentifier(
            sequence_id=self.frame_count,
            timestamp_monotime_us=capture_timestamp * 1000000.0,  # Convert seconds to microseconds
        )
        self.frameReady.emit(FrameData(content=qimage, frame_id=frame_id, source_id=self.source_id))
        if not self.has_warned_frame_count_mismatch and self.frame_count != stream_frame_count:
            logger.warning(f"Frame count mismatch: {self.frame_count} != {stream_frame_count}")
            self.has_warned_frame_count_mismatch = True

        return True

    def _process_application_data(self) -> bool:
        """Process application data from buffer and emit as overlay."""
        # Check if we have application data
        with self.buffer_lock:
            if len(self.overlay_buffer) == 0:
                return False  # No overlay data available
            data = self.overlay_buffer.popleft()

        if self._overlay is None:
            logger.debug("No overlay decoder configured; skipping overlay data.")
            return False

        try:
            xml_data = data.get("data")
            scene = self._overlay.decoder.decode(xml_data)
            if scene is None:
                logger.debug("Decoder did not return a scene; skipping overlay emission.")
                return False

            if not scene.entities:
                logger.debug("No entities found in parsed scene; skipping overlay emission.")
                return False

            self.overlay_count += 1

            # Use scene timestamp for synchronization
            if isinstance(scene.time_slice.start, datetime):
                capture_timestamp = (scene.time_slice.start - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
            else:
                raise ValueError("Scene time_slice.start is not a datetime object")

            # Create FrameIdentifier using scene timestamp (device-provided)
            frame_id = FrameIdentifier(
                sequence_id=self.overlay_count,
                timestamp_monotime_us=capture_timestamp * 1000000.0,  # Convert seconds to microseconds
            )

            overlay_data = OverlayData(
                content=scene,
                frame_id=frame_id,
                source_id=self.source_id,
                metadata={},
            )

            # Emit overlay
            self.emit_overlay(overlay_data)

            return True

        except Exception as e:
            logger.error(f"Error processing application data: {e}", exc_info=True)
            return False

    @property
    def handler_type(self) -> str:
        """Return the configured overlay handler, or empty when overlays are disabled."""
        return self._overlay.handler_type if self._overlay is not None else ""

    def get_filter_config(self) -> FilterConfig | None:
        """Return the filter configuration for this overlay source."""
        if self._overlay is None:
            return None
        if self._overlay.filter_factory is not None:
            return self._overlay.filter_factory()
        return build_default_filter_config()
