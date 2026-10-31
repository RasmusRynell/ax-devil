"""Standalone synchronization engine with no Qt dependencies."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from ax_devil.core.time_units import ms_to_seconds, seconds_to_ms
from ax_devil.modules.settings.logging_config import get_logger

# Generic types for maximum reusability
FrameType = TypeVar("FrameType")
OverlayType = TypeVar("OverlayType")
logger = get_logger(__name__)
LIVE_OVERLAY_MATCH_TOLERANCE_US = 10_000
# Frames wait this long for their overlay. The real metadata-vs-video skew was not measured, so shorter values risk
# dropping every overlay.
OVERLAY_BUFFER_MS = 1500


@dataclass(frozen=True, slots=True)
class TimestampedData(Generic[FrameType]):
    """Container for data with proper timestamp semantics."""

    data: FrameType
    capture_time_us: int  # When data was created/captured, on the media clock
    arrival_at: float  # When sync engine received it, in monotonic seconds


@dataclass(frozen=True, slots=True)
class SyncResult(Generic[FrameType, OverlayType]):
    """Result of synchronization operation."""

    frame: TimestampedData[FrameType]
    overlay: TimestampedData[OverlayType] | None


class StreamSync(Generic[FrameType, OverlayType]):
    """Pure Python synchronization engine for live streaming data.

    Design principles:
    - Timestamp semantics: sync on capture time (media clock), not arrival time
    - Video-only streams deliver immediately; overlays get a buffering allowance (OVERLAY_BUFFER_MS)
    - Event-driven release: inputs release frames when the allowance elapses, or earlier at capacity
    - Idle streams: a buffered tail frame is intentionally not released without another input event
    - Overlay selection: newest update within 10 ms before the frame, including the boundary
    - Future updates wait for later frames; stale updates are discarded before persistence
    - Memory bounded: release oldest frames under pressure instead of evicting them before display
    """

    def __init__(
        self, delay_ms: int = OVERLAY_BUFFER_MS, max_queue_size: int = 100, *, overlay_enabled: bool = True
    ) -> None:
        if delay_ms < 0 or max_queue_size < 1:
            raise ValueError("Buffering delay must be nonnegative and queue capacity must be positive")
        self.delay_s = ms_to_seconds(delay_ms) if overlay_enabled else 0.0
        self._overlay_enabled = overlay_enabled

        self._frame_queue: deque[TimestampedData[FrameType]] = deque(maxlen=max_queue_size)
        self._overlay_queue: deque[TimestampedData[OverlayType]] = deque(maxlen=max_queue_size)
        self._pressure_release_logged = False

        # Callback for emitting synchronized results
        self._emit_callback: Callable[[SyncResult[FrameType, OverlayType]], None] | None = None

    def set_output_callback(self, callback: Callable[[SyncResult[FrameType, OverlayType]], None]) -> None:
        """Set callback for synchronized frame output."""
        self._emit_callback = callback

    def reset(self) -> None:
        """Clear both frame and overlay queues, discarding all buffered data."""
        self._frame_queue.clear()
        self._overlay_queue.clear()
        self._pressure_release_logged = False

    def push_frame(self, frame_data: FrameType, capture_time_us: int) -> None:
        """Add frame data with capture timestamp."""
        timestamped = TimestampedData(data=frame_data, capture_time_us=capture_time_us, arrival_at=time.monotonic())
        self._process_ready_frames()
        if len(self._frame_queue) == self._frame_queue.maxlen:
            if not self._pressure_release_logged:
                logger.warning(
                    f"Live playback buffer is releasing frames before their {seconds_to_ms(self.delay_s):g} ms delay "
                    f"({self._frame_queue.maxlen} frame capacity). High frame rates or bursts shorten overlay "
                    f"matching time; reduce the source frame rate. Reported once until synchronization resets."
                )
                self._pressure_release_logged = True
            self._release_frame()
        self._frame_queue.append(timestamped)
        self._process_ready_frames()

    def push_overlay(self, overlay_data: OverlayType, capture_time_us: int) -> None:
        """Add overlay data with capture timestamp."""
        if not self._overlay_enabled:
            return
        timestamped = TimestampedData(data=overlay_data, capture_time_us=capture_time_us, arrival_at=time.monotonic())
        self._overlay_queue.append(timestamped)
        # Synchronization is deliberately input-driven. Overlay arrivals wake ready-frame processing just like frame
        # arrivals; no timer is needed because an idle live stream has no new presentation work, and the bounded queues
        # prevent idle buffered data from growing.
        self._process_ready_frames()

    def _process_ready_frames(self) -> None:
        """Process frames that have waited long enough."""
        if not self._emit_callback:
            return

        now = time.monotonic()
        deadline = now - self.delay_s

        while self._frame_queue and self._frame_queue[0].arrival_at <= deadline:
            self._release_frame()

    def _release_frame(self) -> None:
        frame = self._frame_queue.popleft()
        selected: TimestampedData[OverlayType] | None = None
        earliest_match_us = frame.capture_time_us - LIVE_OVERLAY_MATCH_TOLERANCE_US
        # Scan the bounded queue so late arrivals cannot hide behind a future update.
        for _ in range(len(self._overlay_queue)):
            candidate = self._overlay_queue.popleft()
            if candidate.capture_time_us > frame.capture_time_us:
                self._overlay_queue.append(candidate)
            elif candidate.capture_time_us >= earliest_match_us and (
                selected is None or candidate.capture_time_us >= selected.capture_time_us
            ):
                selected = candidate
        self._emit(frame, selected)

    def _emit(self, frame: TimestampedData[FrameType], overlay: TimestampedData[OverlayType] | None) -> None:
        if not self._emit_callback:
            return

        result = SyncResult(frame=frame, overlay=overlay)

        self._emit_callback(result)
