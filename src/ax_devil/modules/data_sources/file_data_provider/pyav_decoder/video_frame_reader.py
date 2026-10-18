"""VideoFrameReader: a threaded video frame reader with frame operations and caching."""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_reader_worker import FrameReaderWorker
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_worker import (
    FrameWorker,
    WorkerState,
)
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import PyAvAbstraction
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import VideoOpenConfig
from ax_devil.modules.data_sources.timing_reports import VideoTimingProfile
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class VideoFrameReader:
    """Custom video frame reader with worker thread management."""

    def __init__(
        self,
        video_path: str,
        cache_budget_bytes: int = 256 * 1024 * 1024,
        prefetch_count: int = 30,
        callback_threads: int = 4,
        worker_timeout: float = 5.0,
        cache_path: Path | None = None,
        cache_identifier: str | None = None,
        open_config: VideoOpenConfig | None = None,
    ) -> None:
        # Store parameters for recreating worker
        self._video_path = video_path
        self._cache_budget_bytes = cache_budget_bytes
        self._prefetch_count = prefetch_count
        self._callback_threads = callback_threads
        self._worker_timeout = worker_timeout
        self._cache_path = cache_path
        self._cache_identifier = cache_identifier
        self._open_config = open_config

        self._callback_executor = ThreadPoolExecutor(max_workers=callback_threads, thread_name_prefix="callback")

        # Worker instances will be created in open()
        self._frame_reader_worker: FrameReaderWorker | None = None
        self._worker: FrameWorker | None = None

        # Shutdown coordination
        self._shutting_down = False

        logger.debug(
            f"VideoFrameReader created: id={id(self)}, video={video_path}, cache_budget_bytes={cache_budget_bytes}"
        )

    def _ensure_open(self) -> None:
        """Ensure the reader is in a valid open state, raise exception if not."""
        if self._frame_reader_worker is None or self._worker is None:
            raise RuntimeError("VideoFrameReader is not open. Call open() first.")
        if self._worker.is_stopped:
            raise RuntimeError("VideoFrameReader is stopped. Call open() to restart.")

    # Public API
    def open(self) -> None:
        """Open the video source and start the worker thread."""
        # Don't create duplicate workers if already running
        if self._worker is not None and (self._worker.is_running or self._worker.is_paused):
            logger.debug("VideoFrameReader is already running - ignoring duplicate open()")
            return

        # Create fresh worker instances for each open
        self._frame_reader_worker = FrameReaderWorker(
            video_path=self._video_path,
            cache_budget_bytes=self._cache_budget_bytes,
            prefetch_count=self._prefetch_count,
            cache_path=self._cache_path,
            cache_identifier=self._cache_identifier,
            open_config=self._open_config,
        )

        self._worker = FrameWorker(
            work_executor=self._frame_reader_worker,
            worker_timeout=self._worker_timeout,
            thread_name="VideoFrameReader",
        )

        # Set worker reference for signaling
        self._frame_reader_worker.set_worker(self._worker)

        if self._shutting_down:
            self._callback_executor = ThreadPoolExecutor(
                max_workers=self._callback_threads, thread_name_prefix="callback"
            )
            self._shutting_down = False

        self._worker.start()

    def get_frame_sync(self, frame_index: int, timeout: float = 5.0) -> DecodedFrame | None:
        """Get a specific frame by index, blocking until available or timeout."""
        # Check if we're shutting down first
        if self._shutting_down:
            logger.debug(f"get_frame_sync({frame_index}): rejected due to shutdown")
            return None

        self._ensure_open()

        result: list[DecodedFrame | None] = [None]  # Use list to allow modification in nested function
        completion_event = threading.Event()

        def sync_callback(frame_data: DecodedFrame | None) -> None:
            result[0] = frame_data
            completion_event.set()

        # Double-check shutdown after ensuring open (in case shutdown happened during _ensure_open)
        if self._shutting_down:
            logger.debug(f"get_frame_sync({frame_index}): rejected due to shutdown after _ensure_open")
            return None

        # Request frame (worker will handle cache check and decoding)
        assert self._frame_reader_worker is not None, "Frame reader worker must be available"
        self._frame_reader_worker.get_frame_async(frame_index, sync_callback)

        # Wait for completion or timeout
        if completion_event.wait(timeout=timeout):
            logger.debug(f"get_frame_sync({frame_index}): completed successfully")
            return result[0]
        logger.warning(f"get_frame_sync({frame_index}): timeout after {timeout}s")
        return None

    def get_frame_async(self, frame_index: int, callback: Callable[[DecodedFrame | None], None]) -> None:
        """Request a specific frame by index, receive it asynchronously via callback."""
        self._ensure_open()

        # Wrap user callback to execute via callback executor
        def wrapped_callback(frame_data: DecodedFrame | None) -> None:
            def _call_callback() -> None:
                try:
                    callback(frame_data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

            self._callback_executor.submit(_call_callback)

        assert self._frame_reader_worker is not None, "Frame reader worker must be available"
        self._frame_reader_worker.get_frame_async(frame_index, wrapped_callback)

    def get_total_frames(self) -> int:
        """Return total frame count from the indexed video."""
        self._ensure_open()
        assert self._frame_reader_worker is not None, "Frame reader worker must be available"
        return self._frame_reader_worker.get_total_frames()

    def get_frame_time_us(self, frame_index: int) -> float:
        """Return frame presentation time in microseconds since the first indexed frame."""
        self._ensure_open()
        assert self._frame_reader_worker is not None, "Frame reader worker must be available"
        return self._frame_reader_worker.get_frame_time_us(frame_index)

    def get_frame_times_us(self) -> tuple[int, ...]:
        """Return all frame presentation times in microseconds since the first indexed frame."""
        self._ensure_open()
        assert self._frame_reader_worker is not None, "Frame reader worker must be available"
        return self._frame_reader_worker.get_frame_times_us()

    def get_frame_period_after_s(self, frame_index: int) -> float | None:
        """Return presentation delay from this frame to the next frame."""
        self._ensure_open()
        assert self._frame_reader_worker is not None, "Frame reader worker must be available"
        return self._frame_reader_worker.get_frame_period_after_s(frame_index)

    def analyze_timing_profile(self) -> VideoTimingProfile:
        """Analyze decoded frame cadence for the opened video."""
        self._ensure_open()
        assert self._frame_reader_worker is not None, "Frame reader worker must be available"
        return self._frame_reader_worker.analyze_timing_profile()

    def close(self) -> None:
        """Close the reader and clean up resources."""
        # Set shutdown flag first to reject new requests
        self._shutting_down = True
        logger.debug("VideoFrameReader shutdown flag set, rejecting new requests")

        # Cancel any pending requests by clearing the queue
        if self._frame_reader_worker is not None:
            try:
                # Clear any pending requests in the queue
                while not self._frame_reader_worker._request_queue.empty():
                    try:
                        frame_index, callback = self._frame_reader_worker._request_queue.get_nowait()
                        # Call callback with None to unblock any waiting threads
                        callback(None)
                        logger.debug(f"Cancelled pending frame request for frame {frame_index}")
                    except Exception:
                        break
            except Exception as e:
                logger.debug(f"Error clearing request queue: {e}")

        self._safe_shutdown_executor()

        # Stop worker and clear references properly
        if self._worker is not None:
            self._worker.stop()

        # Clear worker references to break cycles and allow garbage collection
        if self._frame_reader_worker is not None:
            # Ensure cleanup is called explicitly before clearing reference
            try:
                self._frame_reader_worker.cleanup()
            except Exception as e:
                logger.warning(f"Error during frame reader worker cleanup: {e}")
            self._frame_reader_worker = None

        self._worker = None
        logger.debug("VideoFrameReader cleanup completed, all references cleared")

    def _safe_shutdown_executor(self) -> None:
        """Safely shutdown the callback executor."""
        try:
            self._callback_executor.shutdown(wait=True)
        except Exception as e:
            logger.warning(f"Error shutting down callback executor: {e}")

    # Internal state properties for testing
    @property
    def _frame_processor(self) -> "PyAvAbstraction":
        """Access to frame processor for testing purposes."""
        self._ensure_open()
        assert self._frame_reader_worker is not None, "Frame reader worker must be available"
        return self._frame_reader_worker._frame_processor

    # State properties
    @property
    def state(self) -> WorkerState:
        """Get current reader state."""
        if self._worker is None:
            return WorkerState.STOPPED
        return self._worker.state

    @property
    def is_running(self) -> bool:
        """Check if reader is currently processing frames."""
        if self._worker is None:
            return False
        return self._worker.is_running

    @property
    def is_paused(self) -> bool:
        """Check if reader is currently paused."""
        if self._worker is None:
            return False
        return self._worker.is_paused

    @property
    def is_stopped(self) -> bool:
        """Check if reader is currently stopped."""
        if self._worker is None:
            return True
        return self._worker.is_stopped

    # Optional public state management methods (delegated to worker)
    def pause(self) -> None:
        """Pause the worker thread."""
        self._ensure_open()
        assert self._worker is not None, "Worker must be available to pause"
        self._worker.pause()

    def resume(self) -> None:
        """Resume the worker thread."""
        self._ensure_open()
        assert self._worker is not None, "Worker must be available to resume"
        self._worker.resume()
