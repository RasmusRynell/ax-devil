"""Standalone synchronization engine with no Qt dependencies."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Generic, Optional, TypeVar

from ax_devil.modules.diagnostics.metrics_store import remove_instance, set_metric
from ax_devil.modules.settings.logging_config import get_logger

# Generic types for maximum reusability
FrameType = TypeVar("FrameType")
OverlayType = TypeVar("OverlayType")
logger = get_logger(__name__)
LIVE_OVERLAY_MATCH_TOLERANCE_S = 0.010


@dataclass(frozen=True, slots=True)
class TimestampedData(Generic[FrameType]):
    """Container for data with proper timestamp semantics."""

    data: FrameType
    capture_time: float  # When data was created/captured
    arrival_time: float  # When sync engine received it


@dataclass(frozen=True, slots=True)
class SyncResult(Generic[FrameType, OverlayType]):
    """Result of synchronization operation."""

    frame: TimestampedData[FrameType]
    overlay: Optional[TimestampedData[OverlayType]]


class StreamSync(Generic[FrameType, OverlayType]):
    """Pure Python synchronization engine for live streaming data.

    Design principles:
    - Timestamp semantics: sync on capture_time, not arrival_time
    - Event-driven release: frame and overlay arrivals release frames whose delay has elapsed
    - Idle streams: a buffered tail frame is intentionally not released without another input event
    - Overlay selection: newest update within 10 ms before the frame, including the boundary
    - Future updates wait for later frames; stale updates are discarded before persistence
    - Memory bounded: automatic cleanup
    """

    def __init__(self, object_name: str, delay_ms: int = 50, max_queue_size: int = 100):
        self.object_name = object_name
        self.delay_s = delay_ms / 1000.0

        self._frame_queue: Deque[TimestampedData[FrameType]] = deque(maxlen=max_queue_size)
        self._overlay_queue: Deque[TimestampedData[OverlayType]] = deque(maxlen=max_queue_size)
        self._early_eviction_logged = False

        # Callback for emitting synchronized results
        self._emit_callback: Optional[Callable[[SyncResult[FrameType, OverlayType]], None]] = None

    def __del__(self) -> None:
        try:
            logger.debug(f"StreamSync.__del__ id={id(self)} object_name={getattr(self, 'object_name', '<na>')}")
        except Exception:
            pass

    def set_output_callback(self, callback: Callable[[SyncResult[FrameType, OverlayType]], None]) -> None:
        """Set callback for synchronized frame output."""
        self._emit_callback = callback

    def reset(self) -> None:
        """Clear both frame and overlay queues, discarding all buffered data."""
        self._frame_queue.clear()
        self._overlay_queue.clear()
        self._early_eviction_logged = False
        self._record_queue_metrics()
        set_metric(self.object_name, "Overlay offset (ms)", None)

    def push_frame(self, frame_data: FrameType, capture_time: float) -> None:
        """Add frame data with capture timestamp."""
        timestamped = TimestampedData(data=frame_data, capture_time=capture_time, arrival_time=time.monotonic())
        if (
            not self._early_eviction_logged
            and self._frame_queue
            and len(self._frame_queue) == self._frame_queue.maxlen
            and self._frame_queue[0].arrival_time > timestamped.arrival_time - self.delay_s
        ):
            logger.error(
                f"Live playback buffer is discarding frames before they can display "
                f"({self.delay_s * 1000:g} ms delay, {self._frame_queue.maxlen} frame capacity). "
                f"High frame rates or bursts may stall playback; reduce the source frame rate. "
                f"Reported once until synchronization resets."
            )
            self._early_eviction_logged = True
        self._frame_queue.append(timestamped)
        self._process_ready_frames()
        self._record_queue_metrics()

    def push_overlay(self, overlay_data: OverlayType, capture_time: float) -> None:
        """Add overlay data with capture timestamp."""
        timestamped = TimestampedData(data=overlay_data, capture_time=capture_time, arrival_time=time.monotonic())
        self._overlay_queue.append(timestamped)
        # Synchronization is deliberately input-driven. Overlay arrivals wake ready-frame processing just like frame
        # arrivals; no timer is needed because an idle live stream has no new presentation work, and the bounded queues
        # prevent idle buffered data from growing.
        self._process_ready_frames()
        self._record_queue_metrics()

    def _record_queue_metrics(self) -> None:
        set_metric(self.object_name, "Frame queue", len(self._frame_queue))
        set_metric(self.object_name, "Overlay queue", len(self._overlay_queue))

    def cleanup(self) -> None:
        """Remove diagnostic state when the synchronization owner closes."""
        remove_instance(self.object_name)

    def _process_ready_frames(self) -> None:
        """Process frames that have waited long enough."""
        if not self._emit_callback:
            return

        now = time.monotonic()
        deadline = now - self.delay_s

        while self._frame_queue and self._frame_queue[0].arrival_time <= deadline:
            frame = self._frame_queue.popleft()
            selected: TimestampedData[OverlayType] | None = None
            earliest_match = frame.capture_time - LIVE_OVERLAY_MATCH_TOLERANCE_S
            # Scan the bounded queue so late arrivals cannot hide behind a future update.
            for _ in range(len(self._overlay_queue)):
                candidate = self._overlay_queue.popleft()
                if candidate.capture_time > frame.capture_time:
                    self._overlay_queue.append(candidate)
                elif candidate.capture_time >= earliest_match and (
                    selected is None or candidate.capture_time >= selected.capture_time
                ):
                    selected = candidate
            self._emit(frame, selected)

    def _emit(self, frame: TimestampedData[FrameType], overlay: TimestampedData[OverlayType] | None) -> None:
        if not self._emit_callback:
            return

        time_diff: float = 0.0
        if overlay:
            time_diff = frame.capture_time - overlay.capture_time
        set_metric(self.object_name, "Overlay offset (ms)", time_diff * 1000 if overlay else None)

        result = SyncResult(frame=frame, overlay=overlay)

        self._emit_callback(result)
