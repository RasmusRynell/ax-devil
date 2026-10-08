"""VideoFrameReader prototype with frame operations and caching."""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from logging import WARNING
from pathlib import Path
from queue import Empty, Queue

import av

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.cached_frame import CachedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import FrameCache
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache_pool import FrameCachePool
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_worker import (
    BaseWorker,
    FrameWorker,
    WorkerState,
)
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import (
    DecodedFrames,
    PyAvAbstraction,
)
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import VideoOpenConfig
from ax_devil.modules.data_sources.timing_reports import VideoTimingProfile
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

# Structured log payload for jump fallback paths.
JumpLog = tuple[int, str]


class FrameReaderWorker(BaseWorker):
    """Work executor for frame reading operations."""

    def __init__(
        self,
        video_path: str,
        cache_budget_bytes: int,
        prefetch_count: int,
        cache_path: Path | None = None,
        cache_identifier: str | None = None,
        open_config: VideoOpenConfig | None = None,
        cache_pool: FrameCachePool | None = None,
    ) -> None:
        self._frame_processor = PyAvAbstraction(video_path, cache_path, open_config=open_config)
        identifier = cache_identifier or Path(video_path).name
        self._cache = FrameCache(cache_budget_bytes, identifier=identifier, pool=cache_pool)
        self._request_queue: Queue[tuple[int, Callable[[DecodedFrame | None], None]]] = Queue()
        self._prefetch_count = prefetch_count

        # Thread safety lock for PyAV operations
        # While PyAvAbstraction is designed for single-threaded use and all normal
        # operations happen in the worker thread, this lock protects against:
        # 1. Edge cases during worker shutdown/cleanup sequences
        # 2. Potential external access to _frame_processor during worker lifecycle
        # 3. Race conditions if the worker pattern changes in the future
        # The lock is defensive - it's here because we use a worker pattern, not
        # because PyAvAbstraction itself requires thread safety.
        self._pyav_lock = threading.RLock()  # RLock allows nested locking from same thread

        self._current_read_frame = 0
        self._prefetch_started = False
        self._worker: "FrameWorker" | None = None

        self._total_frames = self._frame_processor.get_total_frames()

        logger.debug(
            f"FrameReaderWorker created: id={id(self)}, video={video_path}, "
            f"cache_budget_bytes={cache_budget_bytes}, prefetch={prefetch_count}, total_frames={self._total_frames}"
        )

    def do_work(self) -> bool:
        """Execute one unit of work. Returns True if work was done."""
        # Check for frame requests first (priority)
        has_request = False
        try:
            frame_index, callback = self._request_queue.get_nowait()
        except Empty:
            pass  # No requests queued
        else:
            try:
                self._handle_frame_request(frame_index, callback)
                has_request = True
            except Exception as exc:
                logger.exception(
                    f"Failed to process frame request {frame_index} "
                    f"(decoder_pos={getattr(self._frame_processor, 'current_frame_index', 'unknown')}): {exc}"
                )
                try:
                    callback(None)
                except Exception as callback_exc:
                    logger.error(f"Error notifying caller about frame {frame_index} failure: {callback_exc}")

        # Try to do prefetch work
        did_prefetch = self._do_prefetch_step()

        return has_request or did_prefetch

    def cleanup(self) -> None:
        """Clean up resources when worker stops."""
        try:
            with self._pyav_lock:  # Lock ensures cleanup is atomic even if called during active operations
                # Clear cache first to free all held frames
                self._cache.close()
                logger.debug(f"Cleared frame cache with {self._cache.size()} frames")

                # Close frame processor to release PyAV resources
                self._frame_processor.close()

                # Break circular reference to FrameWorker
                self._worker = None
                logger.debug("FrameReaderWorker cleanup completed, references cleared")
        except Exception as e:
            logger.warning(f"Error closing frame processor: {e}")

    def _do_prefetch_step(self) -> bool:
        """Do one prefetch step if needed. Returns True if work was done."""
        if not self._prefetch_started:
            return False

        # Lock protects PyAV state during sequential frame reading, incase of concurrent cleanup
        with self._pyav_lock:
            current_pos = self._frame_processor.current_frame_index
            target_pos = self._current_read_frame + self._prefetch_count

            logger.debug(
                f"Prefetch check: current_pos={current_pos}, \
                    target_pos={target_pos}, user_frame={self._current_read_frame}"
            )

            # Check if we're beyond the video bounds
            if current_pos >= self._total_frames:
                logger.debug(f"Prefetch complete: current_pos={current_pos} >= total_frames={self._total_frames}")
                return False

            # Check if we need to prefetch more frames
            if current_pos >= target_pos:
                logger.debug(f"Prefetch complete: current_pos={current_pos} >= target_pos={target_pos}")
                return False

            # Check before decoding so a full forward window does not advance past
            # a frame we cannot retain. Actual admission also checks the decoded size.
            current_frame = self._cache.get(self._current_read_frame)
            if current_frame is None or not self._cache.can_prefetch(
                current_pos, current_frame.reserved_bytes, self._current_read_frame
            ):
                return False

            # Prefetch the next frame
            video_frame: av.VideoFrame | None = self._frame_processor.read_next()
            if not self._cache.contains(current_pos):
                if video_frame is not None:
                    logger.debug(f"Prefetching frame {current_pos}")
                    cached_frame = self._create_cached_frame(current_pos, video_frame)
                    if not self._cache.put(current_pos, cached_frame, current_frame=self._current_read_frame):
                        return False
                else:
                    # End of video reached
                    logger.debug(f"End of video reached at frame {current_pos}")
                    return False
            else:
                # Frame already cached, but we still need to advance the decoder
                logger.debug(f"Frame {current_pos} already cached")

            return True

    def _handle_frame_request(self, frame_index: int, callback: Callable[[DecodedFrame | None], None]) -> None:
        """Handle a specific frame request."""
        callback(self.read_decoded_frame(frame_index))

    def read_decoded_frame(self, frame_index: int) -> DecodedFrame | None:
        """Read one frame on the caller's already-serialized execution path."""
        logger.debug(f"Handling frame request: frame_index={frame_index}")

        # Validate frame index early
        with self._pyav_lock:
            if not (0 <= frame_index < self._total_frames):
                logger.error(f"Frame request {frame_index} out of bounds (0-{self._total_frames - 1})")
                return None

        # A live budget change can evict frames at any time. One lookup either
        # retains the frame for this read or falls back to decoding it.
        cached_frame = self._cache.get(frame_index)
        if cached_frame is None:
            cached_frame = self._decode_and_cache_frame(frame_index)
        else:
            logger.debug(f"Cache hit for frame {frame_index}")

        if cached_frame is not None:
            decoded_frame = cached_frame.to_decoded_frame()
        else:
            decoded_frame = None

        self._update_current_read_frame(frame_index)
        return decoded_frame

    def _decode_and_cache_frame(self, frame_index: int) -> CachedFrame | None:
        """Decode frame and cache all intermediate frames.

        Args:
            frame_index: Frame index to decode

        Returns:
            CachedFrame for the target frame, or None if decoding failed
        """
        logger.debug(f"Cache miss for frame {frame_index}, decoding...")

        # Validate frame index before attempting decode
        with self._pyav_lock:
            if not (0 <= frame_index < self._total_frames):
                logger.error(f"Frame index {frame_index} out of bounds (0-{self._total_frames - 1})")
                return None

            current_decoder_pos = self._frame_processor.current_frame_index
            if self._should_decode_sequentially(frame_index, current_decoder_pos):
                decoded_frames = self._read_until(frame_index, current_decoder_pos)
            else:
                decoded_frames = self._jump_to(
                    frame_index,
                    current_decoder_pos,
                    log_this=self._maybe_prefetch_jump_log(frame_index, current_decoder_pos),
                )

        logger.debug(f"Decoded {len(decoded_frames)} frames to reach frame {frame_index}")

        self._cache_decoded_frames(decoded_frames)
        # Convert the admitted wrapper itself; oversized targets still deliver uncached.
        cached_target = self._cache.get(frame_index)
        return cached_target if cached_target is not None else self._extract_target_frame(decoded_frames, frame_index)

    def _should_decode_sequentially(self, frame_index: int, decoder_pos: int) -> bool:
        return self._is_within_prefetch_window(frame_index) and decoder_pos <= frame_index

    def _is_within_prefetch_window(self, frame_index: int) -> bool:
        return self._current_read_frame < frame_index <= self._current_read_frame + self._prefetch_count

    def _maybe_prefetch_jump_log(self, frame_index: int, decoder_pos: int) -> JumpLog | None:
        if not self._is_within_prefetch_window(frame_index):
            return None
        return (
            WARNING,
            f"Requested frame {frame_index} is behind decoder position {decoder_pos}. "
            "Using jump_to() because sequential decoding cannot read backwards "
            "(normal during rapid scrubbing).",
        )

    def _read_until(self, frame_index: int, decoder_pos: int) -> DecodedFrames:
        logger.debug(
            f"Reading until frame {frame_index} from current_read_frame={self._current_read_frame} "
            f"(decoder_pos={decoder_pos})"
        )
        try:
            frames = self._frame_processor.read_until(frame_index)
        except ValueError as exc:
            warning_log: JumpLog = (
                WARNING,
                f"read_until({frame_index}) failed because decoder is ahead (decoder_pos={decoder_pos}): {exc}. "
                "Falling back to jump_to().",
            )
            return self._jump_to(frame_index, decoder_pos, log_this=warning_log)
        return frames

    def _jump_to(
        self,
        frame_index: int,
        decoder_pos: int,
        *,
        log_this: JumpLog | None = None,
    ) -> DecodedFrames:
        if log_this:
            level, message = log_this
            logger.log(level, message)
        else:
            logger.debug(
                f"Jumping to frame {frame_index} from current_read_frame={self._current_read_frame} "
                f"(decoder_pos={decoder_pos})"
            )
        return self._frame_processor.jump_to(frame_index)

    def _cache_decoded_frames(self, decoded_frames: DecodedFrames) -> None:
        """Cache all decoded frames that aren't already cached.

        Args:
            decoded_frames: List of (frame_index, av.VideoFrame) tuples to cache
        """
        for frame_idx, video_frame in decoded_frames:
            if not self._cache.contains(frame_idx):
                cached_frame = self._create_cached_frame(frame_idx, video_frame)
                self._cache.put(frame_idx, cached_frame)
                logger.debug(f"Cached intermediate frame {frame_idx}")

    def _extract_target_frame(self, decoded_frames: DecodedFrames, expected_frame_index: int) -> CachedFrame | None:
        """Extract target frame from decoded frames and validate it.

        Args:
            decoded_frames: List of (frame_index, av.VideoFrame) tuples
            expected_frame_index: Expected frame index

        Returns:
            CachedFrame for target frame, or None if not found/invalid
        """
        if not decoded_frames:
            logger.warning(f"No frames returned from jump_to for frame {expected_frame_index}")
            return None

        # Search for the target frame in the decoded frames list
        for frame_idx, video_frame in decoded_frames:
            if frame_idx == expected_frame_index:
                logger.debug(f"Successfully found target frame {expected_frame_index} in decoded frames")
                return self._create_cached_frame(frame_idx, video_frame)

        # Target frame not found in decoded frames
        logger.warning(f"Target frame {expected_frame_index} not found in {len(decoded_frames)} decoded frames")
        logger.debug(f"Available frames: {[idx for idx, _ in decoded_frames]}")
        return None

    def _update_current_read_frame(self, frame_index: int) -> None:
        """Update current read frame for prefetch logic.

        Args:
            frame_index: New current frame index
        """
        old_frame = self._current_read_frame
        self._current_read_frame = frame_index
        self._prefetch_started = True
        if self._worker is not None:
            self._worker.signal_work_available()
        logger.debug(f"Updated current_read_frame: {old_frame} -> {frame_index}")

    def set_worker(self, worker: "FrameWorker") -> None:
        """Set the worker reference for signaling work availability."""
        self._worker = worker

    def _create_cached_frame(self, frame_index: int, video_frame: av.VideoFrame) -> CachedFrame:
        """Create a cached frame with all source timing facts known by the reader."""
        return CachedFrame(
            frame_index=frame_index,
            video_frame=video_frame,
            timestamp_us=self._frame_processor.get_frame_time_us(frame_index),
            period_after_s=self._frame_processor.get_frame_period_after_s(frame_index),
            source_timing_metadata=self._frame_processor.get_frame_source_timing_metadata(frame_index),
        )

    def get_frame_async(self, frame_index: int, callback: Callable[[DecodedFrame | None], None]) -> None:
        """Request a specific frame by index, receive it asynchronously via callback."""
        self._request_queue.put((frame_index, callback))
        if self._worker is not None:
            self._worker.signal_work_available()

    def prefetch_one(self) -> bool:
        """Decode at most one prefetched frame after an explicit read request."""
        return self._do_prefetch_step()

    def get_total_frames(self) -> int:
        """Return total frame count from the indexed video."""
        return self._total_frames

    def get_frame_time_us(self, frame_index: int) -> float:
        """Return frame presentation time in microseconds since the first indexed frame."""
        with self._pyav_lock:
            return self._frame_processor.get_frame_time_us(frame_index)

    def get_frame_times_us(self) -> tuple[int, ...]:
        """Return all frame presentation times in microseconds since the first indexed frame."""
        with self._pyav_lock:
            return self._frame_processor.get_frame_times_us()

    def get_frame_period_after_s(self, frame_index: int) -> float | None:
        """Return presentation delay from this frame to the next frame."""
        with self._pyav_lock:
            return self._frame_processor.get_frame_period_after_s(frame_index)

    def analyze_timing_profile(self) -> VideoTimingProfile:
        """Analyze decoded frame cadence for the indexed video."""
        with self._pyav_lock:
            return self._frame_processor.frame_index.analyze_timing_profile()


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
