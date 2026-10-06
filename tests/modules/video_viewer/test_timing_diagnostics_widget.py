"""Tests for offline timing diagnostics controls."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ax_devil.modules.data_sources.timing_reports import OverlayAlignmentReport
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy
from ax_devil.modules.video_viewer.timing_diagnostics_widget import OverlayAlignmentIndicator, TimingDiagnosticsWidget


def _alignment_report(*, total_overlay_frames: int, exact_matches: int) -> OverlayAlignmentReport:
    return OverlayAlignmentReport(
        handler_type="test",
        total_video_frames=total_overlay_frames,
        total_overlay_frames=total_overlay_frames,
        exact_matches=exact_matches,
        max_abs_nearest_offset_us=None,
        sample_period_modes_us=(),
        tolerance_us=100_000,
        tolerated_past_matches=0,
    )


def _alignment_report_with_counts(
    *,
    total_video_frames: int,
    total_overlay_frames: int,
    exact_matches: int,
) -> OverlayAlignmentReport:
    return OverlayAlignmentReport(
        handler_type="test",
        total_video_frames=total_video_frames,
        total_overlay_frames=total_overlay_frames,
        exact_matches=exact_matches,
        max_abs_nearest_offset_us=None,
        sample_period_modes_us=(),
        tolerance_us=100_000,
        tolerated_past_matches=0,
    )


def test_alignment_indicator_hidden_when_fully_matched(qtbot: QtBot) -> None:
    """A perfect overlay match should hide the warning indicator."""
    indicator = OverlayAlignmentIndicator()
    qtbot.addWidget(indicator)

    indicator.show()
    indicator.update_alignment_report(_alignment_report(total_overlay_frames=4, exact_matches=3))
    assert indicator.isVisible()

    indicator.update_alignment_report(_alignment_report(total_overlay_frames=4, exact_matches=4))

    assert indicator.isVisible() is False


def test_alignment_indicator_shown_with_tooltip_when_misaligned(qtbot: QtBot) -> None:
    """An imperfect overlay match should show the warning icon and explain what is wrong."""
    indicator = OverlayAlignmentIndicator()
    qtbot.addWidget(indicator)
    indicator.show()

    indicator.update_alignment_report(_alignment_report(total_overlay_frames=4, exact_matches=3))

    assert indicator.isVisible() is True
    assert not indicator.pixmap().isNull()
    tooltip = indicator.toolTip()
    assert "Overlay timestamp match" in tooltip
    assert "no frame within" in tooltip


def test_alignment_indicator_shown_when_rounded_percent_looks_complete(qtbot: QtBot) -> None:
    """A 99.5% match still has an unmatched overlay and should show the indicator."""
    indicator = OverlayAlignmentIndicator()
    qtbot.addWidget(indicator)
    indicator.show()

    indicator.update_alignment_report(
        _alignment_report_with_counts(total_video_frames=200, total_overlay_frames=200, exact_matches=199)
    )

    assert indicator.isVisible() is True


def test_alignment_indicator_shown_for_timeline_end_mismatch(qtbot: QtBot) -> None:
    """A fully matched but shortened overlay clock should show the warning indicator."""
    indicator = OverlayAlignmentIndicator()
    qtbot.addWidget(indicator)
    indicator.show()
    report = OverlayAlignmentReport(
        handler_type="vod_od",
        total_video_frames=4,
        total_overlay_frames=3,
        exact_matches=3,
        max_abs_nearest_offset_us=0,
        sample_period_modes_us=(),
        tolerance_us=100_000,
        tolerated_past_matches=0,
        video_end_timestamp_us=74_433_000,
        overlay_last_timestamp_us=65_700_000,
    )

    indicator.update_alignment_report(report)

    assert indicator.isVisible() is True
    assert "timestamp match 100%" in indicator.toolTip()
    assert "is 8.733 s before" in indicator.toolTip()


def test_alignment_indicator_hidden_when_report_absent(qtbot: QtBot) -> None:
    """No report should hide the warning indicator."""
    indicator = OverlayAlignmentIndicator()
    qtbot.addWidget(indicator)

    indicator.show()
    indicator.update_alignment_report(_alignment_report(total_overlay_frames=4, exact_matches=3))
    assert indicator.isVisible()

    indicator.update_alignment_report(None)

    assert indicator.isVisible() is False


def test_timestamp_fallback_buttons_emit_selected_policy(qtbot: QtBot) -> None:
    """Exact/Previous controls should expose timestamp fallback policy explicitly."""
    widget = TimingDiagnosticsWidget()
    qtbot.addWidget(widget)
    emitted_policies: list[TimestampFallbackPolicy] = []
    widget.timestampFallbackPolicyChanged.connect(emitted_policies.append)

    widget._exact_button.click()  # noqa: SLF001
    assert widget.timestamp_fallback_policy().mode is TimestampFallbackMode.EXACT_ONLY

    widget._previous_button.click()  # noqa: SLF001
    assert widget.timestamp_fallback_policy().mode is TimestampFallbackMode.PREVIOUS_WITH_TOLERANCE
    assert [policy.mode for policy in emitted_policies] == [
        TimestampFallbackMode.EXACT_ONLY,
        TimestampFallbackMode.PREVIOUS_WITH_TOLERANCE,
    ]


def test_set_timestamp_fallback_policy_updates_buttons_without_emitting(qtbot: QtBot) -> None:
    """Programmatic state sync should not look like a user policy change."""
    widget = TimingDiagnosticsWidget()
    qtbot.addWidget(widget)
    emitted_policies: list[TimestampFallbackPolicy] = []
    widget.timestampFallbackPolicyChanged.connect(emitted_policies.append)

    widget.set_timestamp_fallback_policy(
        TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY, tolerance_us=12_000)
    )

    assert widget.timestamp_fallback_policy() == TimestampFallbackPolicy(
        mode=TimestampFallbackMode.EXACT_ONLY,
        tolerance_us=12_000,
    )
    assert emitted_policies == []
