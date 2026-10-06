"""Timing and alignment report models for data-source diagnostics."""

from __future__ import annotations

import bisect
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

_GOOD_ALIGNMENT_RATIO = 0.8
_MIN_TIMELINE_END_WARNING_US = 1_000_000
_TIMELINE_END_WARNING_RATIO = 0.05

OverlayAlignmentBasis = Literal["timestamp", "sequence"]


@dataclass(frozen=True, slots=True)
class TimestampTimeline:
    """Sorted microsecond timestamp timeline with common diagnostics helpers."""

    timestamps_us: tuple[int, ...]

    @classmethod
    def from_values(cls, values: Iterable[int]) -> "TimestampTimeline":
        """Return a sorted timestamp timeline from arbitrary timestamp values."""
        return cls(tuple(sorted(values)))

    @property
    def count(self) -> int:
        """Return number of timestamps in the timeline."""
        return len(self.timestamps_us)

    @property
    def timestamp_set(self) -> set[int]:
        """Return timestamps as a set for membership and overlap checks."""
        return set(self.timestamps_us)

    def deltas_us(self) -> tuple[int, ...]:
        """Return timestamp deltas between adjacent samples."""
        return tuple(
            next_timestamp_us - timestamp_us
            for timestamp_us, next_timestamp_us in zip(self.timestamps_us, self.timestamps_us[1:])
        )

    def delta_modes_us(self, limit: int) -> tuple[tuple[int, int], ...]:
        """Return most common adjacent timestamp deltas."""
        return tuple(Counter(self.deltas_us()).most_common(limit))

    def nearest_offset_us(self, timestamp_us: int) -> int | None:
        """Return signed nearest timeline timestamp offset from ``timestamp_us``."""
        nearest = self.nearest(timestamp_us)
        if nearest is None:
            return None
        _index, nearest_timestamp_us = nearest
        return nearest_timestamp_us - timestamp_us

    def nearest(self, timestamp_us: int) -> tuple[int, int] | None:
        """Return nearest timeline item as ``(index, timestamp_us)``."""
        if not self.timestamps_us:
            return None
        insert_index = bisect.bisect_left(self.timestamps_us, timestamp_us)
        candidates: list[tuple[int, int]] = []
        if insert_index > 0:
            candidates.append((insert_index - 1, self.timestamps_us[insert_index - 1]))
        if insert_index < len(self.timestamps_us):
            candidates.append((insert_index, self.timestamps_us[insert_index]))
        return min(candidates, key=lambda item: abs(item[1] - timestamp_us))


@dataclass(slots=True)
class FrameTimeline:
    """Source-owned video frame timeline with lazy timestamp materialization."""

    count: int
    _timestamp_loader: Callable[[], tuple[int, ...]] | None = field(default=None, repr=False, compare=False)
    _timestamps_us: tuple[int, ...] | None = field(default=None, init=False, repr=False, compare=False)
    _timestamp_timeline: TimestampTimeline | None = field(default=None, init=False, repr=False, compare=False)

    @classmethod
    def from_timestamps(cls, timestamps_us: Iterable[int]) -> "FrameTimeline":
        """Create a frame timeline from an eagerly available timestamp collection."""
        timeline = cls(count=0)
        timeline._timestamps_us = tuple(timestamps_us)
        timeline.count = len(timeline._timestamps_us)
        return timeline

    @classmethod
    def lazy(cls, *, count: int, timestamp_loader: Callable[[], tuple[int, ...]]) -> "FrameTimeline":
        """Create a frame timeline whose timestamps are loaded only when needed."""
        return cls(count=count, _timestamp_loader=timestamp_loader)

    @property
    def timestamps_us(self) -> tuple[int, ...]:
        """Return frame timestamps, materializing them only on first access."""
        if self._timestamps_us is None:
            if self._timestamp_loader is None:
                self._timestamps_us = ()
            else:
                self._timestamps_us = self._timestamp_loader()
        return self._timestamps_us

    @property
    def timestamp_timeline(self) -> TimestampTimeline:
        """Return a sorted timestamp timeline for timestamp-based diagnostics."""
        if self._timestamp_timeline is None:
            self._timestamp_timeline = TimestampTimeline.from_values(self.timestamps_us)
        return self._timestamp_timeline


@dataclass(frozen=True, slots=True)
class VideoTimingProfile:
    """Summary of decoded video frame timing derived from frame PTS values."""

    total_frames: int
    is_variable_cadence: bool
    period_modes_us: tuple[tuple[int, int], ...]

    @property
    def primary_periods_text(self) -> str:
        """Return compact text for the dominant frame periods."""
        if not self.period_modes_us:
            return "unknown"
        return ", ".join(f"{period_us / 1000.0:g} ms" for period_us, _count in self.period_modes_us[:3])


@dataclass(frozen=True, slots=True)
class OverlayAlignmentReport:
    """Summary of how well an overlay timestamp set matches decoded video frame times."""

    handler_type: str
    total_video_frames: int
    total_overlay_frames: int
    exact_matches: int
    max_abs_nearest_offset_us: int | None
    sample_period_modes_us: tuple[tuple[int, int], ...]
    tolerance_us: int
    tolerated_past_matches: int
    alignment_basis: OverlayAlignmentBasis = "timestamp"
    sequence_matches: int = 0
    video_end_timestamp_us: int | None = None
    overlay_last_timestamp_us: int | None = None

    @property
    def policy_matches(self) -> int:
        """Return overlay frames that match exactly or by configured timestamp tolerance."""
        if self.alignment_basis == "sequence":
            return self.sequence_matches
        return self.exact_matches + self.tolerated_past_matches

    @property
    def policy_match_ratio(self) -> float:
        """Return exact-or-tolerated timestamp match ratio for overlay frames."""
        if self.total_overlay_frames <= 0:
            return 1.0
        return self.policy_matches / self.total_overlay_frames

    @property
    def has_poor_policy_alignment(self) -> bool:
        """Return whether configured timestamp matching looks suspiciously poor."""
        return (
            self.total_video_frames > 0
            and self.total_overlay_frames > 0
            and self.policy_match_ratio < _GOOD_ALIGNMENT_RATIO
        )

    @property
    def last_overlay_timestamp_offset_us(self) -> int | None:
        """Return the last overlay sample time relative to the video timeline end."""
        if self.video_end_timestamp_us is None or self.overlay_last_timestamp_us is None:
            return None
        return self.overlay_last_timestamp_us - self.video_end_timestamp_us

    @property
    def has_suspicious_last_overlay_timestamp(self) -> bool:
        """Return whether the last overlay sample is far from the video timeline end."""
        offset_us = self.last_overlay_timestamp_offset_us
        video_end_timestamp_us = self.video_end_timestamp_us
        if offset_us is None or video_end_timestamp_us is None or video_end_timestamp_us <= 0:
            return False
        warning_threshold_us = max(
            _MIN_TIMELINE_END_WARNING_US,
            round(video_end_timestamp_us * _TIMELINE_END_WARNING_RATIO),
        )
        return abs(offset_us) > warning_threshold_us

    @property
    def has_alignment_warning(self) -> bool:
        """Return whether timestamp matching or timeline coverage needs attention."""
        return self.unmatched_overlay_frames > 0 or self.has_suspicious_last_overlay_timestamp

    @property
    def tolerance_text(self) -> str:
        """Return compact text for the configured timestamp tolerance."""
        return f"{self.tolerance_us / 1000.0:g} ms"

    @property
    def unmatched_overlay_frames(self) -> int:
        """Return overlay samples that match no video frame under the current policy."""
        return max(0, self.total_overlay_frames - self.policy_matches)

    @property
    def alignment_issue_text(self) -> str:
        """Return a plain-language explanation of suspicious overlay timing."""
        issues: list[str] = []
        last_overlay_timestamp_offset_us = self.last_overlay_timestamp_offset_us
        if self.has_suspicious_last_overlay_timestamp and last_overlay_timestamp_offset_us is not None:
            direction = "after" if last_overlay_timestamp_offset_us > 0 else "before"
            difference_text = f"{abs(last_overlay_timestamp_offset_us) / 1_000_000.0:g} s"
            issues.append(
                f"Last overlay sample is {difference_text} {direction} the video timeline end. "
                "This may be intentional partial coverage, or the clocks may differ "
                "(for example, generated frame-rate timestamps versus source PTS)."
            )

        unmatched = self.unmatched_overlay_frames
        if unmatched <= 0:
            return " ".join(issues)
        if self.total_video_frames <= 0:
            issues.append("No decoded video frame timestamps are available.")
        elif self.max_abs_nearest_offset_us is not None and self.max_abs_nearest_offset_us > self.tolerance_us:
            worst_offset_text = f"{self.max_abs_nearest_offset_us / 1000.0:g} ms"
            issues.append(
                f"{unmatched} sample(s) up to {worst_offset_text} from the nearest frame, "
                f"beyond {self.tolerance_text} tolerance."
            )
        elif self.alignment_basis == "sequence":
            issues.append(f"{unmatched} sample(s) have no matching video frame number.")
        else:
            issues.append(f"{unmatched} sample(s) have no frame within {self.tolerance_text}.")
        return " ".join(issues)
