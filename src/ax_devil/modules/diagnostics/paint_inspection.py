"""Evidence and comparable baselines for an individual retained paint."""

from __future__ import annotations

from dataclasses import dataclass

from ax_devil.modules.diagnostics.render_metrics import (
    PAINT_STAGES,
    TIMING_STAGES,
    PaintObservation,
    TimingStage,
    TimingSummary,
    ViewerSnapshot,
    summarize,
)

BASELINE_LIMIT = 60

_GUIDANCE = {
    "image": "Compare target dimensions. Image drawing includes scaling; native Qt work needs a native profiler.",
    "compose": (
        "Check drawing build time, rebuild reasons and primitive counts. Preparation also includes cache checks."
    ),
    "draw": "Compare submitted primitives, primitive types and target dimensions. Individual draw calls are not timed.",
    "other": (
        "Setup, hover highlighting and Qt item finalization are not individually timed. "
        "Record a reproduction to narrow the work."
    ),
    "generation": (
        "Check rebuild reasons and scene size. Per-recipe costs are not captured; record a "
        "reproduction to locate Python work."
    ),
    "filter": (
        "Compare input and remaining entities. Filtering can precede painting; it is not an additional paint stage."
    ),
    "submission": (
        "This includes synchronous submission work and Qt scheduling. Record a reproduction "
        "to investigate GUI-thread activity."
    ),
    "interval": (
        "Before-submission time can include normal playback pacing, pause, seek or source "
        "work. It is not proof of a dropped frame."
    ),
}


@dataclass(frozen=True, slots=True)
class InspectionSelection:
    """Identify an inspected observation inside an exported frozen snapshot."""

    viewer_id: str
    sample_index: int
    metric_key: str


@dataclass(frozen=True, slots=True)
class StageComparison:
    """Selected duration versus actual operations in preceding comparable paints."""

    stage: TimingStage
    current_ms: float | None
    baseline: TimingSummary | None

    @property
    def delta_ms(self) -> float | None:
        """Return change from the preceding median, never treating missing work as zero."""
        if self.current_ms is None or self.baseline is None:
            return None
        return self.current_ms - self.baseline.median_ms


@dataclass(frozen=True, slots=True)
class IntervalBreakdown:
    """Disjoint portions of a new-frame completion interval when submission does not overlap."""

    before_submission_ms: float
    submission_wait_ms: float
    paint_ms: float


@dataclass(frozen=True, slots=True)
class PaintInspection:
    """Measured evidence for one paint, without claiming unobserved root causes."""

    viewer: ViewerSnapshot
    index: int
    metric_key: str

    @property
    def observation(self) -> PaintObservation:
        """Return the selected paint, including historical source observations."""
        return self.viewer.history[self.index]

    @property
    def baseline(self) -> tuple[PaintObservation, ...]:
        """Use at most 60 preceding paints of the same kind, excluding the selected event."""
        return tuple(
            item for item in self.viewer.history[: self.index] if item.new_frame == self.observation.new_frame
        )[-BASELINE_LIMIT:]

    @property
    def comparisons(self) -> tuple[StageComparison, ...]:
        """Compare each stage using only actual measured operations."""
        prior = [item.timings for item in self.baseline]
        current = self.observation.timings
        return tuple(
            StageComparison(
                stage,
                current[stage.key],
                summarize([value for item in prior if (value := item[stage.key]) is not None]),
            )
            for stage in TIMING_STAGES
        )

    @property
    def interval_breakdown(self) -> IntervalBreakdown | None:
        """Split a frame gap only when all boundaries are measured and non-overlapping."""
        item = self.observation
        if item.frame_interval_ms is None or item.submission_delay_ms is None:
            return None
        before = item.frame_interval_ms - item.submission_delay_ms - item.sample.paint_ms
        if before < -0.000001:
            return None
        return IntervalBreakdown(max(0.0, before), item.submission_delay_ms, item.sample.paint_ms)

    @property
    def findings(self) -> tuple[str, ...]:
        """Explain measured increases, interval boundaries and the next useful investigation."""
        comparisons = {entry.stage.key: entry for entry in self.comparisons}
        selected = comparisons[self.metric_key]
        if selected.current_ms is None:
            headline = f"{selected.stage.label}: no measurement for this paint."
        else:
            headline = f"{selected.stage.label}: {selected.current_ms:.3f} ms."
            if selected.baseline is not None and selected.delta_ms is not None:
                headline = (
                    f"{headline} {selected.delta_ms:+.3f} ms versus the preceding median "
                    f"({selected.baseline.count} measured operations)."
                )
        findings = [headline]
        key = self.metric_key
        if key == "paint":
            stages = [comparisons[stage.key] for stage in PAINT_STAGES[1:]]
            increases = [stage for stage in stages if stage.delta_ms is not None and stage.delta_ms > 0.0]
            if increases:
                dominant = max(increases, key=lambda stage: stage.delta_ms or 0.0)
                findings.append(
                    f"Largest measured stage increase: {dominant.stage.label} ({dominant.delta_ms:+.3f} ms)."
                )
            else:
                dominant = max(stages, key=lambda stage: stage.current_ms or 0.0)
                findings.append(f"Largest paint stage: {dominant.stage.label} ({dominant.current_ms:.3f} ms).")
            key = dominant.stage.key
        if self.metric_key == "interval":
            gap = self.interval_breakdown
            if gap is not None:
                findings.append(
                    f"Interval = {gap.before_submission_ms:.3f} ms before submission + "
                    f"{gap.submission_wait_ms:.3f} ms waiting to paint + {gap.paint_ms:.3f} ms painting."
                )
            else:
                findings.append(
                    "No additive interval breakdown: submission is unmeasured or overlaps the preceding frame."
                )
        generation = self.observation.sample.generation
        if key in ("compose", "generation") and generation is not None:
            if generation.generation_time_ms is not None:
                reasons = ", ".join(reason.value for reason in generation.build_reasons) or "not reported"
                findings.append(
                    f"Drawing build used {generation.generation_time_ms:.3f} ms of "
                    f"{self.observation.sample.compose_ms:.3f} ms preparation. Rebuild triggers: {reasons}."
                )
            elif generation.drawing_cache_hit:
                findings.append("Prepared drawing was reused; no drawing build was measured on this paint.")
        previous = self.baseline[-1] if self.baseline else None
        if previous is not None:
            current_sample = self.observation.sample
            before = previous.sample
            if (current_sample.width, current_sample.height) != (before.width, before.height):
                findings.append(
                    f"Target changed from {before.width} × {before.height} to "
                    f"{current_sample.width} × {current_sample.height} logical pixels."
                )
            if current_sample.drawn_primitive_count != before.drawn_primitive_count:
                findings.append(
                    f"Primitives submitted for drawing changed from {before.drawn_primitive_count} "
                    f"to {current_sample.drawn_primitive_count}."
                )
        findings.append(_GUIDANCE[key])
        return tuple(findings)

    @property
    def workload(self) -> tuple[tuple[str, str, str], ...]:
        """Show current workload alongside the preceding comparable paint, when retained."""
        previous = self.baseline[-1] if self.baseline else None
        current = _workload(self.observation)
        before = _workload(previous) if previous else {}
        return tuple((label, value, before.get(label, "—")) for label, value in current.items())


def _workload(item: PaintObservation) -> dict[str, str]:
    sample = item.sample
    generation = sample.generation
    return {
        "Frame": sample.frame.label,
        "Overlay": f"{sample.overlay.label}{' · reused' if sample.overlay_reused else ''}"
        if sample.overlay
        else "None",
        "Target": f"{sample.width} × {sample.height} logical px",
        "Primitives": f"{sample.primitive_count} available · {sample.drawn_primitive_count} submitted",
        "Entities": f"{generation.input_entity_count} → {generation.filtered_entity_count}" if generation else "—",
        "Primitive types": ", ".join(f"{count} {kind}" for kind, count in generation.primitive_counts)
        if generation
        else "—",
        "Cache": (
            f"Scene {'reused' if generation.filter_cache_hit else 'built'} · "
            f"drawing {'reused' if generation.drawing_cache_hit else 'built'}"
        )
        if generation
        else "—",
        "Rebuild reasons": ", ".join(reason.value for reason in generation.build_reasons) or "No rebuild"
        if generation
        else "—",
        "Superseded since preceding paint": str(item.superseded_since_previous_paint),
    }
