"""RTSP video transport with optional embedded Scene decoding."""

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

import numpy as np
from ax_devil_rtsp import (
    SceneMetadata,
    StartCancelledError,
    StreamConfig,
    StreamError,
    StreamSession,
    VideoOutput,
    VideoSample,
)
from numpy.typing import NDArray
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.filtering import FilterConfig, FilterFactory, build_default_filter_config
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.settings.logging_config import get_logger

from ..base import FrameSource, OverlaySource, Worker

logger = get_logger(__name__)

CONNECTION_TIMEOUT_S = 10.0


def rtsp_stream_config(*, metadata: bool) -> StreamConfig:
    """Return the session configuration for live video, with embedded scene metadata when requested."""
    return StreamConfig(video=VideoOutput.RGBA, metadata=metadata, timeout=CONNECTION_TIMEOUT_S)


class _RTSPWorker(Worker):
    """Worker that connects the RTSP session and turns its buffered data into frames and overlays."""

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
        parent: QObject | None = None,
        *,
        overlay: RTSPOverlayDecoder | None = None,
    ) -> None:
        super().__init__(parent=parent)

        logger.info(f"Initializing RTSP source {source_id}")

        self.source_id = source_id
        self._overlay = overlay
        self.frame_buffer: deque[VideoSample[NDArray[np.uint8]]] = deque(maxlen=buffer_size)
        self.overlay_buffer: deque[SceneMetadata] = deque(maxlen=buffer_size)
        self.buffer_lock = Lock()
        self.frame_count = 0
        self.overlay_count = 0
        self._session_started = False
        # Orders sourceConnected (worker thread) before sourceError (session thread).
        self._status_lock = Lock()
        self._early_failure = ""
        self._warned_missing_capture_time = False

        self._worker = _RTSPWorker(self, source_id)
        self._own_worker(self._worker)
        self._worker.sourceError.connect(self.sourceError.emit)
        self._worker.sourceFinished.connect(self._on_worker_finished)

        self.session: StreamSession | None = StreamSession(
            rtsp_url,
            rtsp_stream_config(metadata=overlay is not None),
            on_video=self._on_video,
            on_metadata=self._on_metadata if overlay is not None else None,
            on_failure=self._on_failure,
        )

    def _on_worker_finished(self) -> None:
        """Handle worker finished signal."""
        logger.debug(f"Worker finished for {self.source_id}")

    def _on_video(self, sample: VideoSample[NDArray[np.uint8]]) -> None:
        """Keep the newest frames; runs on the session's receive thread."""
        with self.buffer_lock:
            self.frame_buffer.append(sample)

    def _on_metadata(self, document: SceneMetadata) -> None:
        """Keep the newest metadata documents; runs on the session's receive thread."""
        with self.buffer_lock:
            self.overlay_buffer.append(document)

    def _on_failure(self, failure: BaseException) -> None:
        """Report a stream that failed after it started; the session has already logged it."""
        with self._status_lock:
            if self._session_started:
                self.sourceError.emit(f"RTSP Error: {failure}")
            else:
                self._early_failure = f"RTSP Error: {failure}"

    def play(self) -> bool:
        """Start the worker, which connects the RTSP session before processing data."""
        logger.debug(f"Play requested for {self.source_id}")
        if self.session is None:
            return False  # Stopped sources are terminal; reopening creates a new source.
        result = self._worker.play()
        logger.debug(f"Play result: {result}, worker running: {self._worker.is_playing()}")
        return result

    def pause(self) -> None:
        """Pause data processing; the RTSP session stays connected."""
        logger.debug(f"Pause requested for {self.source_id}")
        self._worker.pause()

    def stop(self) -> None:
        """Stop RTSP stream and clean up."""
        logger.debug(f"Stopping {self.source_id}")

        # Stopping the session first also cancels a connection attempt the worker is blocked in. The session's
        # receive thread is not joined: a pending TCP connect keeps it alive until it times out, and once stopped it
        # reports no failure.
        session, self.session = self.session, None
        if session is not None:
            session.stop()
        self._worker.stop()
        if not self._worker.wait(2000):
            logger.warning(f"Worker thread did not exit within 2s for {self.source_id}")

        with self.buffer_lock:
            self.frame_buffer.clear()
            self.overlay_buffer.clear()
        self.frame_count = 0
        self.overlay_count = 0

        self.cleanup_posted_events()
        logger.debug(f"Stopped {self.source_id}")

    def wait(self, timeout: int = 2000) -> bool:
        """Wait for worker to finish."""
        return bool(self._worker.wait(timeout))

    def _process_data(self) -> bool:
        """Connect on the first call, then process both video frames and scene metadata.

        Returns False only when connecting fails or is cancelled. Otherwise the Worker.run() loop handles
        pause/stop — this method never decides thread lifecycle.
        """
        if not self._session_started:
            return self._start_session()

        frame_processed = self._process_video_frame()
        overlay_processed = self._process_metadata()

        if not frame_processed and not overlay_processed:
            time.sleep(0.01)  # Avoid busy-spin when no data available

        return True

    def _start_session(self) -> bool:
        """Connect the RTSP session on the worker thread so the UI never waits for the camera."""
        session = self.session
        if session is None:
            return False
        try:
            session.start()
        except StartCancelledError:
            return False
        except StreamError as exc:
            logger.error(f"Failed to start RTSP: {exc}")
            self.sourceError.emit(f"Failed to start RTSP: {exc}")
            return False
        logger.debug(f"RTSP session started for {self.source_id}")
        with self._status_lock:
            self._session_started = True
            self.sourceConnected.emit()
            if self._early_failure:
                self.sourceError.emit(self._early_failure)
        return True

    def _process_video_frame(self) -> bool:
        """Process video frame from buffer. Returns True if a frame was processed."""
        with self.buffer_lock:
            if len(self.frame_buffer) == 0:
                return False
            sample = self.frame_buffer.popleft()

        if sample.capture_time_ns is None:
            if not self._warned_missing_capture_time:
                logger.warning("RTSP video has no capture time (onvifreplayext=1); frames cannot be synchronized")
                self._warned_missing_capture_time = True
            return False

        pixels = sample.data
        height, width = pixels.shape[:2]
        qimage = QImage(pixels.data, width, height, pixels.strides[0], QImage.Format.Format_RGBX8888).copy()

        self.frame_count += 1
        # For RTSP, use device-provided capture timestamp as monotime
        frame_id = FrameIdentifier(
            sequence_id=self.frame_count,
            timestamp_monotime_us=sample.capture_time_ns / 1000.0,
        )
        self.frameReady.emit(FrameData(content=qimage, frame_id=frame_id, source_id=self.source_id))
        return True

    def _process_metadata(self) -> bool:
        """Decode one buffered scene metadata document and emit it as an overlay."""
        with self.buffer_lock:
            if len(self.overlay_buffer) == 0:
                return False
            document = self.overlay_buffer.popleft()

        if self._overlay is None:
            logger.debug("No overlay decoder configured; skipping overlay data.")
            return False

        try:
            scene = self._overlay.decoder.decode(document.xml)
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

            self.emit_overlay(overlay_data)

            return True

        except Exception as e:
            logger.error(f"Error processing scene metadata: {e}", exc_info=True)
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
