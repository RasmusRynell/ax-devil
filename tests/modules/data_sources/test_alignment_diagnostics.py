"""Tests for overlay alignment diagnostics."""

from unittest.mock import Mock

from ax_devil.modules.data_sources.alignment_diagnostics import (
    analyze_overlay_alignment,
    analyze_overlay_sequence_alignment,
)
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy


def test_alignment_report_flags_poor_exact_match_for_sampled_overlay() -> None:
    """Sampled overlay timestamps should show poor exact match against VFR video PTS."""
    report = analyze_overlay_alignment(
        handler_type="vod_od",
        video_timeline=FrameTimeline.from_timestamps((0, 80_000, 120_000, 200_000, 280_000, 320_000)),
        overlay_timestamps_us={100_000, 200_000, 300_000},
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert report.total_video_frames == 6
    assert report.total_overlay_frames == 3
    assert report.exact_matches == 1
    assert report.tolerated_past_matches == 0
    assert report.policy_matches == 1
    assert report.has_poor_policy_alignment is True
    assert report.sample_period_modes_us == ((100_000, 2),)
    assert report.max_abs_nearest_offset_us == 20_000


def test_alignment_report_accepts_exact_overlay() -> None:
    """Exact overlay timestamps should be reported as healthy."""
    report = analyze_overlay_alignment(
        handler_type="exact",
        video_timeline=FrameTimeline.from_timestamps((0, 40_000, 80_000)),
        overlay_timestamps_us={0, 40_000, 80_000},
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert report.exact_matches == 3
    assert report.policy_match_ratio == 1.0
    assert report.has_poor_policy_alignment is False


def test_alignment_report_detects_short_overlay_timeline() -> None:
    """Matching overlay samples should still warn when their clock ends far before the video clock."""
    report = analyze_overlay_alignment(
        handler_type="vod_od",
        video_timeline=FrameTimeline.from_timestamps((0, 32_000_000, 65_700_000, 74_433_000)),
        overlay_timestamps_us={0, 32_000_000, 65_700_000},
        policy=TimestampFallbackPolicy(tolerance_us=100_000),
    )

    assert report.policy_match_ratio == 1.0
    assert report.video_end_timestamp_us == 74_433_000
    assert report.overlay_last_timestamp_us == 65_700_000
    assert report.has_suspicious_last_overlay_timestamp is True


def test_alignment_report_counts_tolerated_past_overlay_matches() -> None:
    """Overlay timestamps just before video timestamps should count as tolerated matches."""
    report = analyze_overlay_alignment(
        handler_type="mote",
        video_timeline=FrameTimeline.from_timestamps((1_280_000,)),
        overlay_timestamps_us={1_279_000},
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert report.exact_matches == 0
    assert report.tolerated_past_matches == 1
    assert report.policy_matches == 1
    assert report.policy_match_ratio == 1.0
    assert report.has_poor_policy_alignment is False


def test_alignment_report_rejects_overlay_after_video_for_tolerance() -> None:
    """Overlay timestamps after the video timestamp should not count as tolerated matches."""
    report = analyze_overlay_alignment(
        handler_type="mote",
        video_timeline=FrameTimeline.from_timestamps((1_280_000,)),
        overlay_timestamps_us={1_280_001},
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert report.exact_matches == 0
    assert report.tolerated_past_matches == 0
    assert report.policy_matches == 0
    assert report.has_poor_policy_alignment is True


def test_sequence_alignment_counts_frame_number_coverage() -> None:
    """Frame-index overlays should be compared by sequence instead of timestamp."""
    report = analyze_overlay_sequence_alignment(
        handler_type="mot_csv",
        total_video_frames=3,
        overlay_sequences={0, 2, 5},
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert report.alignment_basis == "sequence"
    assert report.total_video_frames == 3
    assert report.total_overlay_frames == 3
    assert report.sequence_matches == 2
    assert report.policy_matches == 2
    assert report.unmatched_overlay_frames == 1
    assert report.alignment_issue_text == "1 sample(s) have no matching video frame number."


def test_sequence_overlay_analysis_does_not_load_video_timestamps() -> None:
    """Sequence-based overlay diagnostics should use frame count without materializing timestamps."""
    timestamps_loaded = False

    def load_timestamps() -> tuple[int, ...]:
        nonlocal timestamps_loaded
        timestamps_loaded = True
        return (0, 40_000, 80_000)

    provider = Mock()
    provider.get_timestamp_fallback_policy.return_value = TimestampFallbackPolicy(tolerance_us=5_000)
    provider.uses_sequence_lookup.return_value = True
    provider.get_available_sequences.return_value = {0, 2, 5}

    source = FileOverlaySource.__new__(FileOverlaySource)
    source._handler_type = "mot_csv"
    source.data_provider = provider

    report = source.analyze_alignment(FrameTimeline.lazy(count=3, timestamp_loader=load_timestamps))

    assert timestamps_loaded is False
    assert report.alignment_basis == "sequence"
    assert report.sequence_matches == 2
