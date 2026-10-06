"""Tests for overlay alignment report diagnostics."""

from __future__ import annotations

from ax_devil.modules.data_sources.timing_reports import OverlayAlignmentReport


def _report(
    *,
    total_overlay_frames: int,
    exact_matches: int,
    tolerated_past_matches: int = 0,
    max_abs_nearest_offset_us: int | None = None,
    tolerance_us: int = 100_000,
    video_end_timestamp_us: int | None = None,
    overlay_last_timestamp_us: int | None = None,
) -> OverlayAlignmentReport:
    return OverlayAlignmentReport(
        handler_type="test",
        total_video_frames=total_overlay_frames,
        total_overlay_frames=total_overlay_frames,
        exact_matches=exact_matches,
        max_abs_nearest_offset_us=max_abs_nearest_offset_us,
        sample_period_modes_us=(),
        tolerance_us=tolerance_us,
        tolerated_past_matches=tolerated_past_matches,
        video_end_timestamp_us=video_end_timestamp_us,
        overlay_last_timestamp_us=overlay_last_timestamp_us,
    )


def test_alignment_issue_text_empty_when_fully_matched() -> None:
    """A fully matched report should report no alignment issue."""
    report = _report(total_overlay_frames=4, exact_matches=4)

    assert report.unmatched_overlay_frames == 0
    assert report.alignment_issue_text == ""


def test_alignment_issue_text_blames_offset_beyond_tolerance() -> None:
    """Offsets beyond the tolerance should be named as the cause and suggest raising tolerance."""
    report = _report(
        total_overlay_frames=4,
        exact_matches=3,
        max_abs_nearest_offset_us=250_000,
        tolerance_us=100_000,
    )

    text = report.alignment_issue_text
    assert "1 sample(s) up to 250 ms" in text
    assert "beyond 100 ms tolerance" in text


def test_alignment_issue_text_blames_sampling_when_within_tolerance() -> None:
    """Unmatched samples without large offsets should point at sampling differences."""
    report = _report(
        total_overlay_frames=4,
        exact_matches=3,
        max_abs_nearest_offset_us=None,
        tolerance_us=100_000,
    )

    text = report.alignment_issue_text
    assert "1 sample(s) have no frame within 100 ms" in text


def test_alignment_issue_text_warns_when_overlay_timeline_ends_early() -> None:
    """A large endpoint difference should warn even when every overlay sample matches."""
    report = _report(
        total_overlay_frames=4,
        exact_matches=4,
        video_end_timestamp_us=74_433_000,
        overlay_last_timestamp_us=65_700_000,
    )

    assert report.has_suspicious_last_overlay_timestamp is True
    assert report.has_alignment_warning is True
    assert "Last overlay sample is 8.733 s before the video timeline end" in report.alignment_issue_text
    assert "intentional partial coverage" in report.alignment_issue_text


def test_small_timeline_end_difference_does_not_warn() -> None:
    """Normal processing tail differences should not be reported as incompatible clocks."""
    report = _report(
        total_overlay_frames=4,
        exact_matches=4,
        video_end_timestamp_us=74_433_000,
        overlay_last_timestamp_us=74_090_000,
    )

    assert report.has_suspicious_last_overlay_timestamp is False
    assert report.has_alignment_warning is False
    assert report.alignment_issue_text == ""
