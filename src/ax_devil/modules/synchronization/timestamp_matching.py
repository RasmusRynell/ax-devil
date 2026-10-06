"""Shared timestamp matching policy for video frames and overlay samples."""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class TimestampFallbackMode(str, Enum):
    """Mode for timestamp fallback after exact and provider-owned lookup fail."""

    EXACT_ONLY = "exact_only"
    PREVIOUS_WITH_TOLERANCE = "previous_with_tolerance"


TimestampMatchType = Literal["exact", "tolerated_past", "retained", "missing"]
DEFAULT_TIMESTAMP_MATCH_TOLERANCE_US = 50_000


@dataclass(frozen=True, slots=True)
class TimestampFallbackPolicy:
    """Timestamp fallback policy applied after exact and provider-owned lookup fail."""

    mode: TimestampFallbackMode = TimestampFallbackMode.PREVIOUS_WITH_TOLERANCE
    tolerance_us: int = DEFAULT_TIMESTAMP_MATCH_TOLERANCE_US

    @property
    def effective_tolerance_us(self) -> int:
        """Return tolerance used by timestamp-only matching under this policy."""
        if self.mode is TimestampFallbackMode.EXACT_ONLY:
            return 0
        return self.tolerance_us

    @property
    def uses_previous_with_tolerance(self) -> bool:
        """Return whether previous timestamp fallback is enabled."""
        return self.mode is TimestampFallbackMode.PREVIOUS_WITH_TOLERANCE


@dataclass(frozen=True, slots=True)
class TimestampMatchResult:
    """Result of matching one video timestamp to an overlay timestamp."""

    overlay_index: int | None
    overlay_timestamp_us: int | None
    offset_us: int | None
    match_type: TimestampMatchType


def find_matching_overlay_timestamp(
    *,
    sorted_overlay_timestamps_us: Sequence[int],
    video_timestamp_us: int,
    policy: TimestampFallbackPolicy,
    allow_previous: bool = False,
) -> TimestampMatchResult:
    """Find the overlay timestamp matching a video timestamp under the shared policy.

    Policy: exact first; otherwise match the nearest overlay timestamp at or before
    the video timestamp when it is within the policy tolerance. With allow_previous,
    older samples are returned as retained and the caller applies its age limit.

    The renderer must never display metadata before the video frame time has
    reached that metadata timestamp. The fallback is intentionally backward-only
    rather than nearest-neighbor matching.
    """
    insert_index = bisect.bisect_right(sorted_overlay_timestamps_us, video_timestamp_us)
    overlay_index = insert_index - 1
    if overlay_index < 0:
        return TimestampMatchResult(None, None, None, "missing")

    overlay_timestamp_us = sorted_overlay_timestamps_us[overlay_index]
    offset_us = overlay_timestamp_us - video_timestamp_us
    if offset_us == 0:
        return TimestampMatchResult(overlay_index, overlay_timestamp_us, offset_us, "exact")
    if policy.uses_previous_with_tolerance and -offset_us <= policy.effective_tolerance_us:
        return TimestampMatchResult(overlay_index, overlay_timestamp_us, offset_us, "tolerated_past")
    if allow_previous:
        return TimestampMatchResult(overlay_index, overlay_timestamp_us, offset_us, "retained")
    return TimestampMatchResult(None, None, None, "missing")


def count_tolerated_past_overlay_timestamps(
    *,
    video_timestamps_us: Sequence[int],
    sorted_overlay_timestamps_us: Sequence[int],
    policy: TimestampFallbackPolicy,
) -> int:
    """Count non-exact overlay timestamps selectable by the shared runtime policy."""
    if not policy.uses_previous_with_tolerance or not video_timestamps_us or not sorted_overlay_timestamps_us:
        return 0

    video_timestamp_set = set(video_timestamps_us)
    tolerated_overlay_timestamps: set[int] = set()
    for video_timestamp_us in video_timestamps_us:
        match = find_matching_overlay_timestamp(
            sorted_overlay_timestamps_us=sorted_overlay_timestamps_us,
            video_timestamp_us=video_timestamp_us,
            policy=policy,
        )
        if (
            match.match_type == "tolerated_past"
            and match.overlay_timestamp_us is not None
            and match.overlay_timestamp_us not in video_timestamp_set
        ):
            tolerated_overlay_timestamps.add(match.overlay_timestamp_us)
    return len(tolerated_overlay_timestamps)
