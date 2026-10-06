"""Rendering diagnostic semantics: progress, bounded history, and truthful timings."""

from dataclasses import replace
from time import perf_counter

import pytest

from ax_devil.modules.diagnostics.metrics_store import set_metrics_enabled
from ax_devil.modules.diagnostics.render_metrics import (
    HISTORY_LIMIT,
    FrameIdentity,
    PaintSample,
    RenderMetricsStore,
    summarize,
)


def sample(at: float, sequence: int = 1) -> PaintSample:
    """Build a CPU paint observation with known, disjoint stage durations."""
    return PaintSample(
        completed_at=at,
        frame=FrameIdentity(sequence, sequence * 40_000),
        overlay=None,
        overlay_reused=False,
        paint_ms=4.0,
        image_ms=1.0,
        compose_ms=1.0,
        draw_ms=1.0,
        primitive_count=0,
        drawn_primitive_count=0,
        generation=None,
        width=640,
        height=480,
    )


def test_submitted_is_not_painted_and_superseded_submissions_are_counted() -> None:
    set_metrics_enabled(True)
    store = RenderMetricsStore()
    store.register("a", "Camera A")
    now = perf_counter()
    store.submit("a", FrameIdentity(0, 0), now=now)
    store.submit("a", FrameIdentity(1, 40_000), now=now + 0.010)
    pending = store.snapshot(now=now + 0.020)[0]
    assert pending.submitted == FrameIdentity(1, 40_000)
    assert pending.last is None
    assert pending.superseded == 1
    store.record("a", sample(now + 0.024), paint_started_at=now + 0.020)
    painted = store.snapshot(now=now + 0.030)[0]
    assert painted.last is not None
    assert painted.last.submission_delay_ms == pytest.approx(10.0)
    assert painted.last.new_frame
    store.record("a", sample(now + 0.044), paint_started_at=now + 0.040)
    repeated = store.snapshot(now=now + 0.050)[0]
    assert repeated.last is not None
    assert not repeated.last.new_frame
    assert repeated.last.submission_delay_ms is None
    assert repeated.frame_rate == repeated.repaint_rate
    assert repeated.timing_summaries["filter"] is None
    assert repeated.timing_summaries["submission"] is not None
    assert repeated.timing_summaries["submission"].count == 1


def test_history_is_bounded_but_idle_viewer_and_last_paint_are_retained() -> None:
    store = RenderMetricsStore()
    store.register("a", "A")
    now = perf_counter()
    for index in range(HISTORY_LIMIT + 20):
        at = now + index / 1000
        store.record("a", sample(at, index), paint_started_at=at - 0.004)
    active = store.snapshot(now=now + 1)[0]
    assert len(active.history) == HISTORY_LIMIT
    idle = store.snapshot(now=now + 70)[0]
    assert not idle.history
    assert idle.last == active.last
    assert idle.frame_rate == 0.0
    store.clear()
    cleared = store.snapshot()[0]
    assert cleared.last is None and cleared.submitted is None
    assert cleared.label == "A"
    store.remove("a")
    assert store.snapshot() == ()


def test_distributions_retain_spikes_between_dashboard_refreshes() -> None:
    store = RenderMetricsStore()
    store.register("a", "A")
    now = perf_counter()
    for index in range(20):
        item = replace(sample(now + index / 100), paint_ms=80.0 if index == 5 else 2.0)
        store.record("a", item, paint_started_at=item.completed_at - 0.002)
    result = store.snapshot(now=now + 1)[0].timing_summaries["paint"]
    assert result is not None
    assert result.median_ms == 2.0
    assert result.maximum_ms == 80.0
    assert result.p95_ms == 2.0
    assert summarize([]) is None


def test_capture_restart_excludes_time_while_metrics_collection_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cadence starts at re-enabling capture, rather than when capture was disabled."""
    from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store

    now = 100.0
    monkeypatch.setattr("ax_devil.modules.diagnostics.render_metrics.perf_counter", lambda: now)
    store = get_render_metrics_store()
    set_metrics_enabled(True)
    store.register("restart-test", "Restart")
    try:
        set_metrics_enabled(False)
        now = 200.0
        set_metrics_enabled(True)
        for index in range(30):
            at = now + (index + 1) / 30
            store.record("restart-test", sample(at, index), paint_started_at=at - 0.004)
        viewer = next(item for item in store.snapshot(now=201.0) if item.viewer_id == "restart-test")
        assert viewer.interval_seconds == 1.0
        assert viewer.frame_rate == 30.0
    finally:
        store.remove("restart-test")


def test_pacing_ignores_repaints_and_cost_distributions_keep_workloads_separate() -> None:
    """An inexpensive repaint cannot hide an expensive new frame or shorten its interval."""
    from ax_devil.modules.diagnostics.render_metrics import PaintSelection

    store = RenderMetricsStore()
    store.register("paced", "Paced")
    start = perf_counter()
    store.record("paced", replace(sample(start, 1), paint_ms=12), paint_started_at=start - 0.012)
    store.record("paced", replace(sample(start + 0.010, 1), paint_ms=1), paint_started_at=start + 0.009)
    store.record("paced", replace(sample(start + 0.040, 2), paint_ms=14), paint_started_at=start + 0.026)
    store.record("paced", replace(sample(start + 0.045, 2), paint_ms=2), paint_started_at=start + 0.043)
    snapshot = store.snapshot(now=start + 0.050)[0]
    assert snapshot.history[0].frame_interval_ms is None
    assert snapshot.history[1].frame_interval_ms is None
    assert snapshot.history[2].frame_interval_ms == pytest.approx(40)
    fresh = snapshot.summaries(PaintSelection.NEW_FRAMES)["paint"]
    repaint = snapshot.summaries(PaintSelection.REPAINTS)["paint"]
    assert fresh is not None and (fresh.count, fresh.median_ms) == (2, 13)
    assert repaint is not None and (repaint.count, repaint.median_ms) == (2, 1.5)
    interval = snapshot.summaries()["interval"]
    assert interval is not None and interval.last_ms == pytest.approx(40)
    # Long pauses remain observable rather than silently disappearing from pacing.
    store.record("paced", sample(start + 20, 3), paint_started_at=start + 19.996)
    last = store.snapshot(now=start + 20)[0].last
    assert last is not None and last.frame_interval_ms == pytest.approx(19_960)


def test_recent_superseded_expires_during_stall_and_reset_preserves_source_links() -> None:
    """Replacement counts do not require painting and source ownership survives reset."""
    store = RenderMetricsStore()
    store.register("pending", "Pending")
    store.set_sources("pending", ("shared-video", "overlay"))
    start = perf_counter()
    for index in range(1000):
        store.submit("pending", sample(start, index).frame, now=start + index / 1000)
    recent = store.snapshot(now=start + 1)[0]
    assert recent.superseded == recent.recent_superseded == 999
    expired = store.snapshot(now=start + 12)[0]
    assert expired.recent_superseded == 0 and expired.superseded == 999
    store.reset("pending")
    reset = store.snapshot(now=start + 12)[0]
    assert reset.superseded == reset.recent_superseded == 0
    assert reset.source_ids == ("shared-video", "overlay")
    store.clear()
    assert store.snapshot()[0].source_ids == reset.source_ids
