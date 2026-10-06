"""Tests for OverlayPersistencePolicy behaviour."""

from __future__ import annotations

from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.video_viewer.overlay_persistence import (
    OverlayPersistencePolicy,
    OverlayPersistenceSettings,
)


def _frame_id(sequence_id: int, timestamp_us: float | None = None) -> FrameIdentifier:
    """Build a FrameIdentifier; timestamp defaults to sequence_id * 33_333 µs (≈30 fps)."""
    ts = timestamp_us if timestamp_us is not None else float(sequence_id) * 33_333.0
    return FrameIdentifier(sequence_id=sequence_id, timestamp_monotime_us=ts)


def _make_overlay(sequence_id: int, timestamp_us: float | None = None) -> OverlayData:
    """Create a minimal OverlayData for testing."""
    scene = Scene(time_slice=TimeSlice(sequence_id, sequence_id))
    return OverlayData(content=scene, frame_id=_frame_id(sequence_id, timestamp_us), source_id="test")


def test_policy_disabled_passthrough() -> None:
    """When disabled the policy must not hold state or alter overlays."""
    policy = OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=False))
    overlay = _make_overlay(1)

    selection = policy.select_overlay(overlay.frame_id, overlay)

    assert selection.overlay is overlay
    assert selection.reused is False
    assert selection.effective_opacity == 1.0

    # Subsequent None should remain None without reuse
    selection_none = policy.select_overlay(overlay.frame_id, None)
    assert selection_none.overlay is None
    assert selection_none.reused is False


def test_policy_reuses_overlay_within_timeout() -> None:
    """Policy should reuse cached overlay while video time has not elapsed."""
    settings = OverlayPersistenceSettings(enabled=True, timeout_ms=2050, opacity=0.4)
    policy = OverlayPersistencePolicy(settings)
    overlay = _make_overlay(5)

    fresh = policy.select_overlay(overlay.frame_id, overlay)
    assert fresh.overlay is overlay
    assert fresh.reused is False
    assert fresh.effective_opacity == 1.0

    # One frame later (≈33 ms video time) — well within the 2050 ms timeout
    next_frame = _frame_id(6)
    reused = policy.select_overlay(next_frame, None)
    assert reused.overlay is overlay
    assert reused.reused is True
    assert reused.effective_opacity == settings.opacity


def test_policy_timeout_clears_cache() -> None:
    """After video-time timeout the cached overlay should expire."""
    settings = OverlayPersistenceSettings(enabled=True, timeout_ms=1000, opacity=0.7)
    policy = OverlayPersistencePolicy(settings)
    overlay = _make_overlay(0, timestamp_us=0.0)

    policy.select_overlay(overlay.frame_id, overlay)

    # Advance by 2 seconds of video time
    later = _frame_id(60, timestamp_us=2_000_000.0)
    expired = policy.select_overlay(later, None)
    assert expired.overlay is None
    assert expired.reused is False


def test_policy_single_step_within_video_timeout_reuses_overlay() -> None:
    """Adjacent frames (~33 ms apart in video time) must not trigger the timeout."""
    # This also confirms that real-time pauses between steps are irrelevant:
    # the timeout is measured purely in video time, so no wall-clock tricks are needed.
    settings = OverlayPersistenceSettings(enabled=True, timeout_ms=1000, opacity=0.7)
    policy = OverlayPersistencePolicy(settings)

    # Store overlay at frame 7 (video time 0 ms)
    overlay = _make_overlay(7, timestamp_us=0.0)
    policy.select_overlay(overlay.frame_id, overlay)

    # Step to frame 8 — video time advances by only 33 ms
    next_frame = _frame_id(8, timestamp_us=33_333.0)
    still_reused = policy.select_overlay(next_frame, None)
    assert still_reused.overlay is overlay
    assert still_reused.reused is True


def test_live_policy_rejects_future_and_expired_samples() -> None:
    """Received samples must be eligible before presentation, even on first arrival."""
    policy = OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=True, timeout_ms=1000))
    overlay = _make_overlay(10, timestamp_us=10_000_000)
    assert policy.select_overlay(_frame_id(9, 9_000_000), overlay).overlay is None
    assert policy.select_overlay(_frame_id(12, 12_000_000), overlay).overlay is None
    assert policy.select_overlay(overlay.frame_id, overlay).overlay is overlay
    policy.update_settings(OverlayPersistenceSettings(enabled=True, timeout_ms=None))
    assert policy.select_overlay(_frame_id(9, 9_000_000), None).overlay is None


def test_late_live_sample_does_not_replace_newer_state() -> None:
    """Out-of-order older updates cannot replace the latest eligible live sample."""
    policy = OverlayPersistencePolicy(OverlayPersistenceSettings.default_enabled())
    newer = _make_overlay(10, 10_000_000)
    older = _make_overlay(9, 9_000_000)
    policy.select_overlay(newer.frame_id, newer)
    selected = policy.select_overlay(_frame_id(11, 10_500_000), older)
    assert selected.overlay is newer
    assert selected.reused
