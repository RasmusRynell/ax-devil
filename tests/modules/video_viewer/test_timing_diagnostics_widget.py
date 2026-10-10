"""Tests for offline timing diagnostics controls."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from ax_devil.modules.data_sources.timing_reports import OverlayAlignmentReport
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy
from ax_devil.modules.video_viewer.timing_diagnostics_widget import OverlayAlignmentIndicator, TimingDiagnosticsWidget
from tests.helpers.widgets import button


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


def _shown_indicator(qtbot: QtBot) -> OverlayAlignmentIndicator:
    indicator = OverlayAlignmentIndicator()
    qtbot.addWidget(indicator)
    indicator.show()
    return indicator


def test_alignment_indicator_hides_when_report_is_fully_matched_or_absent(qtbot: QtBot) -> None:
    indicator = _shown_indicator(qtbot)
    for clearing_report in (_alignment_report(total_overlay_frames=4, exact_matches=4), None):
        indicator.update_alignment_report(_alignment_report(total_overlay_frames=4, exact_matches=3))
        assert indicator.isVisible()

        indicator.update_alignment_report(clearing_report)

        assert not indicator.isVisible()


def test_alignment_indicator_explains_a_single_unmatched_overlay(qtbot: QtBot) -> None:
    """A 99.5% match rounds to 100% but still has an unmatched overlay worth showing."""
    indicator = _shown_indicator(qtbot)

    indicator.update_alignment_report(_alignment_report(total_overlay_frames=200, exact_matches=199))

    assert indicator.isVisible()
    assert not indicator.pixmap().isNull()
    assert "no frame within" in indicator.toolTip()


def test_alignment_indicator_shown_for_timeline_end_mismatch(qtbot: QtBot) -> None:
    """A fully matched but shortened overlay clock should show the warning indicator."""
    indicator = _shown_indicator(qtbot)
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

    assert indicator.isVisible()
    assert "8.733 s" in indicator.toolTip()


def test_timestamp_fallback_buttons_emit_selected_policy(qtbot: QtBot) -> None:
    """Exact/Previous controls should expose timestamp fallback policy explicitly."""
    widget = TimingDiagnosticsWidget()
    qtbot.addWidget(widget)
    emitted_policies: list[TimestampFallbackPolicy] = []
    widget.timestampFallbackPolicyChanged.connect(emitted_policies.append)

    button(widget, "Exact").click()
    assert widget.timestamp_fallback_policy().mode is TimestampFallbackMode.EXACT_ONLY

    button(widget, "Previous").click()
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
    policy = TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY, tolerance_us=12_000)

    widget.set_timestamp_fallback_policy(policy)

    assert widget.timestamp_fallback_policy() == policy
    assert button(widget, "Exact").isChecked()
    assert not button(widget, "Previous").isChecked()
    assert emitted_policies == []
