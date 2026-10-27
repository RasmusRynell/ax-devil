"""RTSP video transport with optional embedded Scene decoding."""

import time
from collections import deque
from dataclasses import dataclass
from queue import Empty, SimpleQueue
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

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.core.time_units import ns_to_us
from ax_devil.modules.filtering import FilterConfig, FilterFactory, build_default_filter_config
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.settings.logging_config import get_logger

from ..base import FrameSource, OverlaySource, Worker

logger = get_logger(__name__)

VIDEO_SILENCE_S = 5.0
RETRY_INITIAL_DELAY_S = 0.25
RETRY_MAX_DELAY_S = 2.0


def rtsp_stream_config(*, metadata: bool, timeout_s: float) -> StreamConfig:
    """Return the session configuration for live video, with embedded scene metadata when requested."""
    return StreamConfig(video=VideoOutput.RGBA, metadata=metadata, timeout=timeout_s)


class _RTSPWorker(Worker):
    """Worker that connects the RTSP session and turns its buffered data into frames and overlays."""

    def __init__(self, rtsp_source: "RTSPSource", source_id: str) -> None:
        super().__init__(source_id)
        self.rtsp_source = rtsp_source

    def run_loop(self) -> bool:
        """Process frames and overlays from RTSP buffers."""
        try:
            return self.rtsp_source._process_data()
        except Cancelled:
            return False


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
        timeout_s: float,
    ) -> None:
        super().__init__(parent=parent)

        logger.info(f"Initializing RTSP source {source_id}")

        self.source_id = source_id
        self._rtsp_url = rtsp_url
        self._overlay = overlay
        self._timeout_s = timeout_s
        self.frame_buffer: deque[VideoSample[NDArray[np.uint8]]] = deque(maxlen=buffer_size)
        self.overlay_buffer: deque[SceneMetadata] = deque(maxlen=buffer_size)
        self.buffer_lock = Lock()
        self.frame_count = 0
        self.overlay_count = 0
        self._session_started = False
        self._session_lock = Lock()
        self._stop_token = CancelToken()
        self._failures: SimpleQueue[tuple[str, bool]] = SimpleQueue()
        self._retry_delay_s = RETRY_INITIAL_DELAY_S
        self._last_video_received_at = time.monotonic()
        self._video_stall_reported = False
        # Orders sourceConnected before a failure from the session thread.
        self._status_lock = Lock()
        self._early_failure: tuple[str, bool] | None = None
        self._warned_missing_capture_time = False

        self._worker = _RTSPWorker(self, source_id)
        self._own_worker(self._worker)
        self._worker.sourceError.connect(self.sourceError.emit)
        self._worker.sourceFinished.connect(self._on_worker_finished)

        self.session: StreamSession | None = self._new_session()

    def _new_session(self) -> StreamSession:
        """Build a fresh one-shot RTSP session with this source's callbacks."""
        return StreamSession(
            self._rtsp_url,
            rtsp_stream_config(metadata=self._overlay is not None, timeout_s=self._timeout_s),
            on_video=self._on_video,
            on_metadata=self._on_metadata if self._overlay is not None else None,
            on_failure=self._on_failure,
        )

    def _on_worker_finished(self) -> None:
        """Handle worker finished signal."""
        logger.debug(f"Worker finished for {self.source_id}")

    def _on_video(self, sample: VideoSample[NDArray[np.uint8]]) -> None:
        """Keep the newest frames; runs on the session's receive thread."""
        with self._status_lock:
            self._last_video_received_at = time.monotonic()
            self._video_stall_reported = False
        with self.buffer_lock:
            self.frame_buffer.append(sample)

    def _on_metadata(self, document: SceneMetadata) -> None:
        """Keep the newest metadata documents; runs on the session's receive thread."""
        with self.buffer_lock:
            if len(self.overlay_buffer) == self.overlay_buffer.maxlen:
                logger.warning(
                    f"Dropping oldest undecoded RTSP metadata document: buffer full; "
                    f"source={self.source_id!r}. Sparse decoder state may be incomplete."
                )
            self.overlay_buffer.append(document)

    def _on_failure(self, failure: BaseException) -> None:
        """Queue a failed session for worker-thread replacement."""
        reason = f"RTSP stream ended: {failure}"
        retryable = self._is_retryable_failure(failure)
        with self._status_lock:
            if self._session_started:
                self._failures.put((reason, retryable))
                reconnecting = True
            else:
                self._early_failure = (reason, retryable)
                reconnecting = False
        if reconnecting:
            (self.sourceReconnecting if retryable else self.sourceError).emit(reason)

    @staticmethod
    def _is_retryable_failure(failure: BaseException) -> bool:
        """Return false for clear credential or unsupported-track errors."""
        message = str(failure).lower()
        return not (
            "with 401 " in message
            or "with 403 " in message
            or "keepalive authentication was refused" in message
            or "has no h.264 or h.265 video" in message
        )

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
        self._stop_token.cancel()

        # Stopping the session first also cancels a connection attempt the worker is blocked in. The session's
        # receive thread is not joined: a pending TCP connect keeps it alive until it times out, and once stopped it
        # reports no failure.
        with self._session_lock:
            session, self.session = self.session, None
        if session is not None:
            session.stop()
        self._worker.stop()
        if not self._worker.wait(2000):
            logger.warning(f"Worker thread did not exit within 2s for {self.source_id}")

        with self.buffer_lock:
            self.frame_buffer.clear()
            if self.overlay_buffer:
                logger.warning(
                    f"Dropping {len(self.overlay_buffer)} undecoded RTSP metadata documents on stop: "
                    f"source={self.source_id!r}"
                )
            self.overlay_buffer.clear()
        self.frame_count = 0
        self.overlay_count = 0

        self.cleanup_posted_events()
        logger.debug(f"Stopped {self.source_id}")

    def wait(self, timeout_ms: int = 2000) -> bool:
        """Wait for worker to finish."""
        return bool(self._worker.wait(timeout_ms))

    def _process_data(self) -> bool:
        """Connect on the first call, then process both video frames and scene metadata.

        Transient failures and a silent video track replace the RTSP session; terminal failures and stop end the worker.
        """
        self._check_video_silence()
        try:
            reason, retryable = self._failures.get_nowait()
        except Empty:
            pass
        else:
            if not retryable:
                return False
            logger.warning(reason)
            with self._session_lock:
                session = self.session
            if session is None:
                return False
            return self._retry_session(session)

        if not self._session_started:
            return self._start_session()

        frame_processed = self._process_video_frame()
        overlay_processed = self._process_metadata()

        if not frame_processed and not overlay_processed:
            time.sleep(0.01)  # Avoid busy-spin when no data available

        return True

    def _check_video_silence(self) -> None:
        """Reconnect when video frames stop, even if metadata keeps the RTSP session active."""
        with self._status_lock:
            silent_s = time.monotonic() - self._last_video_received_at
            if not self._session_started or self._video_stall_reported or silent_s < VIDEO_SILENCE_S:
                return
            self._video_stall_reported = True

        reason = f"No RTSP video frames received for {silent_s:.1f} s"
        self._failures.put((reason, True))
        self.sourceReconnecting.emit(reason)

    def _start_session(self) -> bool:
        """Connect the RTSP session on the worker thread so the UI never waits for the camera."""
        with self._session_lock:
            session = self.session
        if session is None:
            return False
        with self._status_lock:
            self._last_video_received_at = time.monotonic()
            self._video_stall_reported = False
        try:
            session.start()
        except StartCancelledError:
            return False
        except StreamError as exc:
            reason = f"Failed to start RTSP: {exc}"
            if not self._is_retryable_failure(exc):
                logger.error(reason)
                self.sourceError.emit(reason)
                return False
            logger.warning(reason)
            self.sourceReconnecting.emit(reason)
            return self._retry_session(session)
        logger.debug(f"RTSP session started for {self.source_id}")
        with self._status_lock:
            self._session_started = True
            self.sourceConnected.emit()
            if self._early_failure:
                reason, retryable = self._early_failure
                self._early_failure = None
                self._failures.put((reason, retryable))
                signal = self.sourceReconnecting if retryable else self.sourceError
                signal.emit(reason)
        self._retry_delay_s = RETRY_INITIAL_DELAY_S
        return True

    def _retry_session(self, session: StreamSession) -> bool:
        """Replace a finished session after an interruptible, bounded-backoff delay."""
        session.stop()
        while session.is_running:
            session.join(timeout=0.1)
            self._stop_token.raise_if_cancelled()
        with self.buffer_lock:
            self.frame_buffer.clear()
            if self.overlay_buffer:
                logger.warning(
                    f"Dropping {len(self.overlay_buffer)} undecoded RTSP metadata documents on reconnect: "
                    f"source={self.source_id!r}. Sparse decoder state may be incomplete."
                )
            self.overlay_buffer.clear()

        self._stop_token.wait(self._retry_delay_s)
        self._retry_delay_s = min(self._retry_delay_s * 2, RETRY_MAX_DELAY_S)

        with self._status_lock:
            self._session_started = False
        with self._session_lock:
            self._stop_token.raise_if_cancelled()
            self.session = self._new_session()
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
        # For RTSP, use the device-provided capture time
        frame_id = FrameIdentifier(
            sequence_id=self.frame_count,
            timestamp_us=ns_to_us(sample.capture_time_ns),
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
            logger.warning(f"Dropping RTSP metadata before decoding: no decoder configured; source={self.source_id!r}")
            return False

        try:
            scene = self._overlay.decoder.decode(document.xml)
            if scene is None:
                logger.warning(f"No scene emitted for RTSP metadata: decoder returned None; source={self.source_id!r}")
                return False

            if not scene.entities:
                logger.warning(
                    f"Dropping decoded empty RTSP scene instead of emitting it: source={self.source_id!r}. "
                    "Previously displayed objects may remain visible."
                )
                return False

            self.overlay_count += 1

            # Use scene timestamp for synchronization
            capture_time_us = scene.time_slice.start_timestamp_us
            if capture_time_us is None:
                raise ValueError("Scene time_slice.start is not a datetime object")

            # Create FrameIdentifier using scene timestamp (device-provided)
            frame_id = FrameIdentifier(sequence_id=self.overlay_count, timestamp_us=capture_time_us)

            overlay_data = OverlayData(
                content=scene,
                frame_id=frame_id,
                source_id=self.source_id,
                metadata={},
            )

            self.emit_overlay(overlay_data)

            return True

        except Exception as e:
            logger.error(
                f"Dropping RTSP metadata after processing failed: source={self.source_id!r}, "
                f"error={type(e).__name__}. Sparse decoder state may be incomplete.",
                exc_info=True,
            )
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
