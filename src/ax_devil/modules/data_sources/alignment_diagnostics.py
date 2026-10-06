"""Diagnostics for video frame timestamps and overlay timestamp alignment."""

from __future__ import annotations

from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport, TimestampTimeline
from ax_devil.modules.synchronization.timestamp_matching import (
    TimestampFallbackPolicy,
    count_tolerated_past_overlay_timestamps,
)


def analyze_overlay_alignment(
    *,
    handler_type: str,
    video_timeline: FrameTimeline,
    overlay_timestamps_us: set[int],
    policy: TimestampFallbackPolicy,
) -> OverlayAlignmentReport:
    """Compare overlay timestamp keys with decoded video frame timestamps."""
    video_timestamp_timeline = video_timeline.timestamp_timeline
    overlay_timeline = TimestampTimeline.from_values(overlay_timestamps_us)
    video_timestamp_set = video_timestamp_timeline.timestamp_set

    exact_matches = sum(1 for timestamp_us in overlay_timeline.timestamps_us if timestamp_us in video_timestamp_set)
    tolerated_past_matches = count_tolerated_past_overlay_timestamps(
        video_timestamps_us=video_timestamp_timeline.timestamps_us,
        sorted_overlay_timestamps_us=overlay_timeline.timestamps_us,
        policy=policy,
    )
    nearest_offsets_us = [
        offset_us
        for overlay_timestamp_us in overlay_timeline.timestamps_us
        if (offset_us := video_timestamp_timeline.nearest_offset_us(overlay_timestamp_us)) is not None
    ]
    video_end_timestamp_us = (
        video_timestamp_timeline.timestamps_us[-1] if video_timestamp_timeline.timestamps_us else None
    )
    overlay_last_timestamp_us = overlay_timeline.timestamps_us[-1] if overlay_timeline.timestamps_us else None

    return OverlayAlignmentReport(
        handler_type=handler_type,
        total_video_frames=video_timeline.count,
        total_overlay_frames=overlay_timeline.count,
        exact_matches=exact_matches,
        max_abs_nearest_offset_us=max((abs(offset_us) for offset_us in nearest_offsets_us), default=None),
        sample_period_modes_us=overlay_timeline.delta_modes_us(5),
        tolerance_us=policy.effective_tolerance_us,
        tolerated_past_matches=tolerated_past_matches,
        video_end_timestamp_us=video_end_timestamp_us,
        overlay_last_timestamp_us=overlay_last_timestamp_us,
    )


def analyze_overlay_sequence_alignment(
    *,
    handler_type: str,
    total_video_frames: int,
    overlay_sequences: set[int],
    policy: TimestampFallbackPolicy,
) -> OverlayAlignmentReport:
    """Compare overlay sequence identifiers with decoded video frame numbers."""
    sequence_matches = sum(1 for sequence_id in overlay_sequences if 0 <= sequence_id < total_video_frames)

    return OverlayAlignmentReport(
        handler_type=handler_type,
        total_video_frames=total_video_frames,
        total_overlay_frames=len(overlay_sequences),
        exact_matches=0,
        max_abs_nearest_offset_us=None,
        sample_period_modes_us=(),
        tolerance_us=policy.effective_tolerance_us,
        tolerated_past_matches=0,
        alignment_basis="sequence",
        sequence_matches=sequence_matches,
    )
