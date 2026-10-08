"""Single-worker delivery runtime for one seekable file video source."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ax_devil.core.playback_speed import DEFAULT_PLAYBACK_SPEED, scale_frame_period
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache_pool import FrameCachePool
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_worker import BaseWorker, FrameWorker
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import FrameReaderWorker
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import VideoOpenConfig
from ax_devil.modules.data_sources.timing_reports import VideoTimingProfile
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class _SynchronousRead:
    """One blocking decoded-frame request completed by the delivery worker."""

    completed: threading.Event
    result: DecodedFrame | None = None


@dataclass(slots=True)
class _FrameRequest:
    """A decoded-frame request owned by the delivery worker."""

    frame_number: int
    callback: Callable[[DecodedFrame | None], None] | None = None
    position_generation: int | None = None
    synchronous_read: _SynchronousRead | None = None


class FileFrameDelivery(BaseWorker):
    """Serialize playback, on-demand reads, prefetch, delivery, and shutdown for one file source."""

    def __init__(
        self,
        *,
        decoder: FrameReaderWorker,
        fps: float,
        source_id: str,
        on_playback_frame: Callable[[DecodedFrame, int], None],
        on_playback_finished: Callable[[], None],
    ) -> None:
        self._decoder = decoder
        self._fps = fps
        self._on_playback_frame = on_playback_frame
        self._on_playback_finished = on_playback_finished
        self._condition = threading.Condition()
        self._requests: deque[_FrameRequest] = deque()
        self._playback_request: _FrameRequest | None = None
        self._current_frame_index = -1
        self._position_generation = 0
        self._playback_speed = DEFAULT_PLAYBACK_SPEED
        self._playing = False
        self._closed = False
        self._prefetch_pending = False
        self._next_deadline: float | None = None
        self._worker = FrameWorker(self, thread_name=f"{source_id}-frame-delivery")

    @classmethod
    def create(
        cls,
        *,
        video_path: str,
        fps: float,
        source_id: str,
        cache_pool: FrameCachePool,
        prefetch_count: int,
        cache_path: Path | None,
        open_config: VideoOpenConfig | None,
        on_playback_frame: Callable[[DecodedFrame, int], None],
        on_playback_finished: Callable[[], None],
    ) -> "FileFrameDelivery":
        """Create one delivery runtime with its source-local decoded-frame cache."""
        return cls(
            decoder=FrameReaderWorker(
                video_path=video_path,
                cache_budget_bytes=0,
                cache_pool=cache_pool,
                prefetch_count=prefetch_count,
                cache_path=cache_path,
                cache_identifier=source_id,
                open_config=open_config,
            ),
            fps=fps,
            source_id=source_id,
            on_playback_frame=on_playback_frame,
            on_playback_finished=on_playback_finished,
        )

    def open(self) -> None:
        """Start the one worker that may access this source's decoder."""
        self._worker.start()

    def close(self) -> None:
        """Stop delivery, cancel queued reads, and release decoder resources."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._playing = False
            self._condition.notify_all()
        self._worker.stop()

    def wait(self, timeout_ms: int = 2000) -> bool:
        """Wait for delivery shutdown."""
        return self._worker.wait(timeout_ms)

    def play(self) -> bool:
        """Begin paced sequential frame delivery."""
        with self._condition:
            if self._closed:
                return False
            self._playing = True
            self._next_deadline = None
            self._condition.notify_all()
        return True

    def pause(self) -> None:
        """Stop paced playback while allowing explicit read requests to finish."""
        with self._condition:
            self._playing = False
            self._next_deadline = None
            self._condition.notify_all()

    def jump_to(self, frame_number: int) -> None:
        """Request one exact frame and make it the next playback position."""
        with self._condition:
            if self._closed:
                return
            self._position_generation += 1
            self._current_frame_index = frame_number
            self._playback_request = _FrameRequest(
                frame_number=frame_number,
                position_generation=self._position_generation,
            )
            self._next_deadline = None
            self._condition.notify_all()

    def request_frame_async(self, frame_number: int, callback: Callable[[DecodedFrame | None], None]) -> None:
        """Queue one non-playback decoded-frame request."""
        with self._condition:
            if self._closed:
                callback(None)
                return
            self._requests.append(_FrameRequest(frame_number=frame_number, callback=callback))
            self._condition.notify_all()

    def read_decoded_frame(self, frame_number: int, timeout: float = 5.0) -> DecodedFrame | None:
        """Read one frame for a non-playback caller through the delivery worker."""
        synchronous_read = _SynchronousRead(completed=threading.Event())
        with self._condition:
            if self._closed:
                return None
            self._requests.append(_FrameRequest(frame_number=frame_number, synchronous_read=synchronous_read))
            self._condition.notify_all()
        if synchronous_read.completed.wait(timeout):
            return synchronous_read.result
        logger.warning(f"Timed out reading frame {frame_number} from file delivery")
        return None

    def set_fps(self, fps: float) -> None:
        """Set the fallback playback cadence after source metadata is available."""
        with self._condition:
            self._fps = fps

    def set_playback_speed(self, speed: float) -> None:
        """Set the speed used for subsequent playback deadlines."""
        with self._condition:
            self._playback_speed = speed
            self._next_deadline = None
            self._condition.notify_all()

    def get_playback_speed(self) -> float:
        """Return the active playback speed."""
        with self._condition:
            return self._playback_speed

    def get_current_frame(self) -> int:
        """Return the most recently requested playback frame."""
        with self._condition:
            return self._current_frame_index

    def get_position_generation(self) -> int:
        """Return the generation assigned to the active explicit position."""
        with self._condition:
            return self._position_generation

    def get_total_frames(self) -> int:
        """Return the indexed source frame count."""
        return self._decoder.get_total_frames()

    def get_cached_ranges(self) -> tuple[tuple[int, int], ...]:
        """Return the frames that are decoded and cached, as inclusive ``(first, last)`` runs."""
        return self._decoder.get_cached_ranges()

    def get_frame_time_us(self, frame_number: int) -> float:
        """Return one frame's presentation time in microseconds since the first frame."""
        return self._decoder.get_frame_time_us(frame_number)

    def try_frame_time_us(self, frame_number: int) -> float | None:
        """Return one frame's presentation time, or None without waiting when the decoder is busy."""
        return self._decoder.try_frame_time_us(frame_number)

    def get_frame_period_after_s(self, frame_number: int) -> float | None:
        """Return the source cadence for one frame."""
        return self._decoder.get_frame_period_after_s(frame_number)

    def get_frame_times_us(self) -> tuple[int, ...]:
        """Return decoded source timestamps for diagnostics."""
        return self._decoder.get_frame_times_us()

    def analyze_timing_profile(self) -> VideoTimingProfile:
        """Return decoder-owned cadence diagnostics."""
        return self._decoder.analyze_timing_profile()

    def do_work(self) -> bool:
        """Perform one prioritized delivery, prefetch, or paced playback action."""
        request: _FrameRequest | None = None
        prefetch = False
        playback_finished = False

        with self._condition:
            while not self._closed:
                if self._playback_request is not None:
                    request = self._playback_request
                    self._playback_request = None
                    break
                if self._requests:
                    request = self._requests.popleft()
                    break
                if self._playing:
                    request, playback_finished, wait_seconds = self._next_playback_request()
                    if request is not None or playback_finished:
                        break
                    if self._prefetch_pending:
                        prefetch = True
                        break
                    if wait_seconds is not None:
                        self._condition.wait(timeout=wait_seconds)
                    continue
                if self._prefetch_pending:
                    prefetch = True
                    break
                self._condition.wait()

            if self._closed:
                return False

        if playback_finished:
            self._on_playback_finished()
            return True

        if prefetch:
            if not self._decoder.prefetch_one():
                with self._condition:
                    self._prefetch_pending = False
            return True

        assert request is not None, "A delivery work item must be selected"
        decoded_frame = self._decoder.read_decoded_frame(request.frame_number)
        self._complete_request(request, decoded_frame)
        return True

    def cleanup(self) -> None:
        """Release the decoder and unblock read callers when the worker stops."""
        pending_requests: list[_FrameRequest]
        with self._condition:
            self._closed = True
            self._playing = False
            pending_requests = list(self._requests)
            self._requests.clear()
            if self._playback_request is not None:
                pending_requests.append(self._playback_request)
                self._playback_request = None
            self._condition.notify_all()

        self._decoder.cleanup()
        for request in pending_requests:
            self._complete_request(request, None)

    def _next_playback_request(self) -> tuple[_FrameRequest | None, bool, float | None]:
        next_frame = self._current_frame_index + 1
        total_frames = self.get_total_frames()
        if next_frame >= total_frames:
            self._playing = False
            self._next_deadline = None
            return None, True, None

        now = time.perf_counter()
        deadline = self._next_deadline
        if deadline is None:
            deadline = now
        if now < deadline:
            return None, False, deadline - now

        # Late playback follows the clock: skip frames whose successor is already due, but always deliver the last one.
        period = self._frame_period(next_frame)
        while now >= deadline + period and next_frame + 1 < total_frames:
            deadline += period
            next_frame += 1
            period = self._frame_period(next_frame)
        self._next_deadline = deadline + period
        self._current_frame_index = next_frame
        return (
            _FrameRequest(
                frame_number=next_frame,
                position_generation=self._position_generation,
            ),
            False,
            None,
        )

    def _frame_period(self, frame_number: int) -> float:
        period = self._decoder.get_frame_period_after_s(frame_number)
        if period is not None and period > 0.0:
            return scale_frame_period(period, self._playback_speed)
        return scale_frame_period(1.0 / self._fps, self._playback_speed)

    def _complete_request(self, request: _FrameRequest, decoded_frame: DecodedFrame | None) -> None:
        with self._condition:
            self._prefetch_pending = decoded_frame is not None

        if request.synchronous_read is not None:
            request.synchronous_read.result = decoded_frame
            request.synchronous_read.completed.set()
            return

        if request.callback is not None:
            try:
                request.callback(decoded_frame)
            except Exception as exc:
                logger.error(f"Error delivering requested frame {request.frame_number}: {exc}")
            return

        if decoded_frame is None:
            with self._condition:
                self._playing = False
            self._on_playback_finished()
            return

        assert request.position_generation is not None, "Playback frames require a position generation"
        self._on_playback_frame(decoded_frame, request.position_generation)
