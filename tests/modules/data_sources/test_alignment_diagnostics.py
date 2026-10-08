"""Tests for overlay alignment diagnostics."""

from pathlib import Path

from ax_devil.modules.data_sources.alignment_diagnostics import (
    analyze_overlay_alignment,
)
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy
from ax_devil.plugins.decoders.mot.provider import MOTChallengeSceneDataProvider


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
    assert report.alignment_issue_text == "2 sample(s) up to 20 ms from the nearest frame, beyond 5 ms tolerance."


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
    assert report.unmatched_overlay_frames == 0
    assert report.alignment_issue_text == ""


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
    assert report.has_alignment_warning is True
    assert "Last overlay sample is 8.733 s before the video timeline end" in report.alignment_issue_text
    assert "intentional partial coverage" in report.alignment_issue_text


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
    assert report.unmatched_overlay_frames == 0
    assert report.alignment_issue_text == ""


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
    assert report.alignment_issue_text == "1 sample(s) have no frame within 5 ms."


def test_small_timeline_end_difference_does_not_warn() -> None:
    """Normal processing tail differences should not be reported as incompatible clocks."""
    report = analyze_overlay_alignment(
        handler_type="partial",
        video_timeline=FrameTimeline.from_timestamps((0, 74_090_000, 74_433_000)),
        overlay_timestamps_us={0, 74_090_000},
        policy=TimestampFallbackPolicy(tolerance_us=100_000),
    )

    assert report.has_suspicious_last_overlay_timestamp is False
    assert report.has_alignment_warning is False
    assert report.alignment_issue_text == ""


def test_sequence_overlay_alignment_counts_coverage_without_loading_video_timestamps(tmp_path: Path) -> None:
    """A real sequence provider reports coverage without materializing the video's timestamp index."""

    def load_timestamps() -> tuple[int, ...]:
        raise AssertionError("Sequence alignment must not load video timestamps")

    path = tmp_path / "detections.txt"
    path.write_text(
        "1,7,100,100,50,50,0.9,1,0.8\n3,7,100,100,50,50,0.9,1,0.8\n6,7,100,100,50,50,0.9,1,0.8\n",
        encoding="utf-8",
    )
    source = FileOverlaySource(path, MOTChallengeSceneDataProvider, "MOT_FILE")
    try:
        report = source.analyze_alignment(FrameTimeline.lazy(count=3, timestamp_loader=load_timestamps))
    finally:
        source.close()

    assert report.alignment_basis == "sequence"
    assert report.total_video_frames == 3
    assert report.total_overlay_frames == 3
    assert report.sequence_matches == 2
    assert report.policy_matches == 2
    assert report.unmatched_overlay_frames == 1
    assert report.alignment_issue_text == "1 sample(s) have no matching video frame number."
