"""Regression checks for paint-level evidence, baseline selection and historical context."""

from dataclasses import replace
from time import perf_counter

import pytest

from ax_devil.modules.diagnostics.metrics_store import get_metrics_store, set_metrics_enabled
from ax_devil.modules.diagnostics.paint_inspection import PaintInspection
from ax_devil.modules.diagnostics.render_metrics import PaintObservation, RenderMetricsStore, ViewerSnapshot
from ax_devil.modules.video_player.engine.data_types import DrawingBuildReason, DrawingPreparationMetrics
from tests.helpers.render_metrics import sample


def _viewer(*observations: PaintObservation) -> ViewerSnapshot:
    return ViewerSnapshot("inspect", "Camera", None, observations[-1], observations, 0, 10.0)


def test_spike_attributes_stage_increase_without_comparing_to_repaints_or_future_frames() -> None:
    """A slow overlay build is compared with earlier new-frame work, not cached repaints."""
    normal = PaintObservation(sample(10, 1), None, True)
    repaint = PaintObservation(replace(sample(11, 1), paint_ms=0.5, compose_ms=0.1), None, False)
    slow = PaintObservation(
        replace(
            sample(12, 2),
            paint_ms=40,
            compose_ms=37,
            primitive_count=100,
            generation=DrawingPreparationMetrics(generation_time_ms=35, build_reasons=(DrawingBuildReason.TARGET,)),
        ),
        2.0,
        True,
        2000.0,
    )
    future = PaintObservation(replace(sample(13, 3), paint_ms=1000, compose_ms=997), None, True)
    inspection = PaintInspection(_viewer(normal, repaint, slow, future), 2, "paint")
    comparisons = {row.stage.key: row for row in inspection.comparisons}
    assert inspection.baseline == (normal,)
    assert comparisons["compose"].delta_ms == 36
    assert comparisons["generation"].baseline is None
    assert "Prepare overlays (+36.000 ms)" in inspection.findings[1]
    assert any("Drawing target changed" in current for _, current, _ in inspection.workload)
    assert inspection.observation.sample.frame.sequence == 2


def test_long_interval_separates_delivery_wait_and_paint_without_inventing_a_cause() -> None:
    """A long source/pacing gap is not misdiagnosed as slow painting."""
    observation = PaintObservation(sample(12), 5.0, True, 100.0)
    inspection = PaintInspection(_viewer(observation), 0, "interval")
    gap = inspection.interval_breakdown
    assert gap is not None
    assert gap.before_submission_ms == 91
    assert gap.submission_wait_ms == 5
    assert gap.paint_ms == 4
    assert any("normal playback pacing" in finding for finding in inspection.findings)
    overlap = replace(observation, submission_delay_ms=120.0)
    assert PaintInspection(_viewer(overlap), 0, "interval").interval_breakdown is None
    unmeasured = replace(observation, submission_delay_ms=None)
    assert PaintInspection(_viewer(unmeasured), 0, "interval").interval_breakdown is None


def test_first_paint_has_no_baseline_and_repaints_compare_only_to_repaints() -> None:
    """No history is shown as unavailable, never as a zero-cost baseline."""
    first = PaintObservation(sample(10), None, True)
    repaint = PaintObservation(sample(11), None, False)
    next_repaint = PaintObservation(sample(12), None, False)
    viewer = _viewer(first, repaint, next_repaint)
    assert not PaintInspection(viewer, 0, "paint").baseline
    assert all(row.delta_ms is None for row in PaintInspection(viewer, 1, "paint").comparisons)
    assert PaintInspection(viewer, 2, "paint").baseline == (repaint,)


def test_paint_context_survives_source_updates_removal_and_superseded_submissions() -> None:
    """Inspection retains the historical fields instead of borrowing current source state."""
    set_metrics_enabled(True)
    sources = get_metrics_store()
    store = RenderMetricsStore()
    store.register("context", "Context")
    store.set_sources("context", ("linked",))
    sources.set_metric("linked", "Frame queue", 7)
    sources.set_metric("unlinked", "Frame queue", 999)
    now = perf_counter()
    try:
        store.submit("context", sample(now, 1).frame, now=now - 0.030)
        store.submit("context", sample(now, 2).frame, now=now - 0.020)
        store.record("context", sample(now, 2), paint_started_at=now - 0.004)
        observation = store.snapshot(now=now)[0].last
        assert observation is not None
        assert observation.superseded_since_previous_paint == 1
        assert observation.submission_delay_ms == pytest.approx(16)
        assert set(observation.sources) == {"linked"}
        sources.set_metric("linked", "Frame queue", 0)
        sources.remove_instance("linked")
        assert observation.sources["linked"].observations["Frame queue"].value == 7
        assert observation.source_context_at is not None
        store.record("context", sample(now + 0.010, 2), paint_started_at=now + 0.006)
        latest = store.snapshot(now=now + 0.010)[0].last
        assert latest is not None and latest.superseded_since_previous_paint == 0
    finally:
        sources.remove_instance("linked")
        sources.remove_instance("unlinked")
