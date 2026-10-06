"""Atomic paint samples and bounded, viewer-owned rendering history."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from statistics import median
from threading import RLock
from time import perf_counter
from typing import TYPE_CHECKING

from ax_devil.modules.diagnostics.metrics_gate import is_metrics_enabled, register_metrics_listener
from ax_devil.modules.diagnostics.metrics_store import SourceSnapshot, get_metrics_store

if TYPE_CHECKING:
    from ax_devil.modules.video_player.engine.data_types import DrawingPreparationMetrics

HISTORY_SECONDS = 10.0
HISTORY_LIMIT = 600


@dataclass(frozen=True, slots=True)
class TimingStage:
    """Named measurement with its scope and relationship to paint work."""

    key: str
    label: str
    description: str


PAINT_STAGES = (
    TimingStage(
        "paint",
        "CPU render preparation",
        "GUI preparation only; excludes scene graph rendering and presentation.",
    ),
    TimingStage(
        "image",
        "Video image",
        "Retain image and update viewport; GPU upload is not measured.",
    ),
    TimingStage(
        "compose",
        "Prepare overlays",
        "Evaluate catalogs and prepare final geometry/text, or retrieve a cached drawing.",
    ),
    TimingStage("draw", "Submit overlays", "Bind prepared data to retained Qt items; excludes GPU drawing."),
    TimingStage(
        "other", "Other CPU work", "Remaining measured setup/finalization; excludes asynchronous Quick rendering."
    ),
)
BUILD_STAGES = (
    TimingStage(
        "generation",
        "Drawing build",
        "Catalog plus geometry/text preparation; included in Prepare overlays. Cache hits excluded.",
    ),
    TimingStage("filter", "Scene filter build", "Filter work since the last retrieval; may precede painting."),
)
DELIVERY_STAGES = (
    TimingStage(
        "interval", "New-frame interval", "Time between changed-frame paint completions, including pauses and seeks."
    ),
    TimingStage(
        "submission", "Submission → paint start", "Includes submission work and Qt scheduling; first paint only."
    ),
)
TIMING_STAGES = (*PAINT_STAGES, *BUILD_STAGES, *DELIVERY_STAGES)
TIMING_BY_KEY = {stage.key: stage for stage in TIMING_STAGES}


class PaintSelection(str, Enum):
    """Select comparable paint observations for cost distributions."""

    ALL = "All paints"
    NEW_FRAMES = "New frames"
    REPAINTS = "Repaints"

    def includes(self, observation: PaintObservation) -> bool:
        """Return whether this observation belongs to the selection."""
        return self is PaintSelection.ALL or observation.new_frame == (self is PaintSelection.NEW_FRAMES)


@dataclass(frozen=True, slots=True)
class FrameIdentity:
    """Source identity; media timestamps are never interpreted as wall-clock latency."""

    sequence: int | None
    timestamp_us: float

    @property
    def label(self) -> str:
        """Return a readable source frame identity."""
        sequence = "?" if self.sequence is None else str(self.sequence)
        return f"#{sequence} · {self.timestamp_us / 1_000_000:.6f} s"


@dataclass(frozen=True, slots=True)
class PaintSample:
    """One completed CPU paint, with disjoint paint stages and separate preparation work."""

    completed_at: float
    frame: FrameIdentity
    overlay: FrameIdentity | None
    overlay_reused: bool
    paint_ms: float
    image_ms: float
    compose_ms: float
    draw_ms: float
    primitive_count: int
    drawn_primitive_count: int
    generation: DrawingPreparationMetrics | None
    width: int
    height: int
    backend: str = "Qt Quick"

    @property
    def timings(self) -> dict[str, float | None]:
        """Return stage durations; filtering may precede this paint and generation is inside compose."""
        return {
            "paint": self.paint_ms,
            "image": self.image_ms,
            "compose": self.compose_ms,
            "draw": self.draw_ms,
            "other": max(0.0, self.paint_ms - self.image_ms - self.compose_ms - self.draw_ms),
            "filter": self.generation.filter_time_ms if self.generation else None,
            "generation": self.generation.generation_time_ms if self.generation else None,
        }


@dataclass(frozen=True, slots=True)
class PaintObservation:
    """A paint sample with submission latency and source-frame progress."""

    sample: PaintSample
    submission_delay_ms: float | None
    new_frame: bool
    frame_interval_ms: float | None = None
    sources: dict[str, SourceSnapshot] = field(default_factory=dict)
    source_context_at: float | None = None
    superseded_since_previous_paint: int = 0

    @property
    def timings(self) -> dict[str, float | None]:
        """Return paint work and independently measured delivery timings."""
        return {**self.sample.timings, "submission": self.submission_delay_ms, "interval": self.frame_interval_ms}


@dataclass(frozen=True, slots=True)
class TimingSummary:
    """Distribution of measured operations, excluding unavailable/cache-hit durations."""

    count: int
    last_ms: float
    median_ms: float
    p95_ms: float
    maximum_ms: float


def summarize(values: list[float]) -> TimingSummary | None:
    """Summarize real observations using the nearest-rank 95th percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return TimingSummary(
        len(values), values[-1], median(ordered), ordered[math.ceil(len(ordered) * 0.95) - 1], ordered[-1]
    )


@dataclass(frozen=True, slots=True)
class ViewerSnapshot:
    """Consistent viewer state and recent paint history, retained while idle."""

    viewer_id: str
    label: str
    submitted: FrameIdentity | None
    last: PaintObservation | None
    history: tuple[PaintObservation, ...]
    superseded: int
    interval_seconds: float
    recent_superseded: int = 0
    source_ids: tuple[str, ...] = ()

    @property
    def frame_rate(self) -> float:
        """Return distinct source-frame paints per second in the observation window."""
        return sum(item.new_frame for item in self.history) / self.interval_seconds if self.interval_seconds else 0.0

    @property
    def repaint_rate(self) -> float:
        """Return paints of an unchanged source frame per second."""
        return (
            sum(not item.new_frame for item in self.history) / self.interval_seconds if self.interval_seconds else 0.0
        )

    def observations(self, selection: PaintSelection) -> tuple[PaintObservation, ...]:
        """Return recent paints matching the selected workload."""
        return tuple(item for item in self.history if selection.includes(item))

    def summaries(self, selection: PaintSelection = PaintSelection.ALL) -> dict[str, TimingSummary | None]:
        """Summarize actual work without mixing new frames and repaints unless requested."""
        values: dict[str, list[float]] = {stage.key: [] for stage in TIMING_STAGES}
        for item in self.observations(selection):
            for key, value in item.timings.items():
                if value is not None:
                    values[key].append(value)
        return {key: summarize(samples) for key, samples in values.items()}

    @property
    def timing_summaries(self) -> dict[str, TimingSummary | None]:
        """Return distributions for all recent paints."""
        return self.summaries()

    @property
    def drawing_cache_reuse(self) -> tuple[int, int]:
        """Return reused and observed drawing retrievals in the recent window."""
        metrics = [item.sample.generation for item in self.history if item.sample.generation is not None]
        return sum(item.drawing_cache_hit for item in metrics), len(metrics)


@dataclass
class _Viewer:
    label: str
    started_at: float
    submitted: FrameIdentity | None = None
    submitted_at: float | None = None
    last: PaintObservation | None = None
    superseded: int = 0
    source_ids: tuple[str, ...] = ()
    last_new_frame_at: float | None = None
    last_paint_superseded: int = 0
    superseded_events: deque[tuple[float, int]] = field(default_factory=deque)
    history: deque[PaintObservation] = field(default_factory=lambda: deque(maxlen=HISTORY_LIMIT))


class RenderMetricsStore:
    """Track viewer lifetimes, submissions, and atomic paint observations."""

    def __init__(self) -> None:
        self._viewers: dict[str, _Viewer] = {}
        self._lock = RLock()

    def register(self, viewer_id: str, label: str) -> None:
        """Register or relabel a viewer without losing its history."""
        with self._lock:
            if viewer_id in self._viewers:
                self._viewers[viewer_id].label = label
            else:
                self._viewers[viewer_id] = _Viewer(label, perf_counter())

    def set_sources(self, viewer_id: str, source_ids: tuple[str, ...]) -> None:
        """Associate a viewer with its exact source instances, including shared sources."""
        with self._lock:
            self._viewers[viewer_id].source_ids = source_ids

    def remove(self, viewer_id: str) -> None:
        """Remove a closed viewer and release its samples."""
        with self._lock:
            self._viewers.pop(viewer_id, None)

    def clear(self) -> None:
        """Reset captured data while retaining registered viewer identities."""
        with self._lock:
            self._viewers = {
                key: _Viewer(value.label, perf_counter(), source_ids=value.source_ids)
                for key, value in self._viewers.items()
            }

    def reset(self, viewer_id: str) -> None:
        """Clear a viewer when its displayed content is cleared."""
        with self._lock:
            if viewer_id in self._viewers:
                self._viewers[viewer_id] = _Viewer(
                    self._viewers[viewer_id].label, perf_counter(), source_ids=self._viewers[viewer_id].source_ids
                )

    def submit(self, viewer_id: str, frame: FrameIdentity, *, now: float | None = None) -> None:
        """Record selection; count pending submissions replaced before a paint."""
        if not is_metrics_enabled():
            return
        with self._lock:
            viewer = self._viewers.get(viewer_id)
            if viewer is None:
                return
            submitted_at = perf_counter() if now is None else now
            self._prune_superseded(viewer, submitted_at)
            if viewer.submitted_at is not None:
                viewer.superseded += 1
                # Aggregate into 100 ms buckets, bounded by the recent window even during stalls.
                bucket = math.floor(submitted_at * 10) / 10
                if viewer.superseded_events and viewer.superseded_events[-1][0] == bucket:
                    _, count = viewer.superseded_events.pop()
                    viewer.superseded_events.append((bucket, count + 1))
                else:
                    viewer.superseded_events.append((bucket, 1))
            viewer.submitted = frame
            viewer.submitted_at = submitted_at

    def record(self, viewer_id: str, sample: PaintSample, *, paint_started_at: float) -> None:
        """Publish one completed paint with latency measured only on its first submission paint."""
        if not is_metrics_enabled():
            return
        with self._lock:
            viewer = self._viewers.get(viewer_id)
            if viewer is None:
                return
            delay = None
            if viewer.submitted_at is not None and viewer.submitted == sample.frame:
                delay = max(0.0, (paint_started_at - viewer.submitted_at) * 1000)
                viewer.submitted_at = None
            new_frame = viewer.last is None or viewer.last.sample.frame != sample.frame
            interval = None
            if new_frame:
                if viewer.last_new_frame_at is not None:
                    interval = max(0.0, (sample.completed_at - viewer.last_new_frame_at) * 1000)
                viewer.last_new_frame_at = sample.completed_at
            sources = get_metrics_store().snapshot(viewer.source_ids) if viewer.source_ids else {}
            observation = PaintObservation(
                sample,
                delay,
                new_frame,
                interval,
                sources=sources,
                source_context_at=perf_counter() if sources else None,
                superseded_since_previous_paint=viewer.superseded - viewer.last_paint_superseded,
            )
            viewer.last_paint_superseded = viewer.superseded
            viewer.last = observation
            viewer.history.append(observation)

    def snapshot(self, *, now: float | None = None) -> tuple[ViewerSnapshot, ...]:
        """Return recent samples without expiring idle viewers or their last painted frame."""
        current = perf_counter() if now is None else now
        with self._lock:
            result = []
            for key, viewer in self._viewers.items():
                self._prune_superseded(viewer, current)
                history = tuple(
                    item for item in viewer.history if item.sample.completed_at >= current - HISTORY_SECONDS
                )
                start = max(viewer.started_at, current - HISTORY_SECONDS)
                if len(viewer.history) == HISTORY_LIMIT and history:
                    start = max(start, history[0].sample.completed_at)
                result.append(
                    ViewerSnapshot(
                        key,
                        viewer.label,
                        viewer.submitted,
                        viewer.last,
                        history,
                        viewer.superseded,
                        max(0.0, current - start),
                        sum(count for _, count in viewer.superseded_events),
                        viewer.source_ids,
                    )
                )
            return tuple(sorted(result, key=lambda item: (item.label.lower(), item.viewer_id)))

    @staticmethod
    def _prune_superseded(viewer: _Viewer, now: float) -> None:
        while viewer.superseded_events and viewer.superseded_events[0][0] < now - HISTORY_SECONDS:
            viewer.superseded_events.popleft()


_STORE = RenderMetricsStore()


def get_render_metrics_store() -> RenderMetricsStore:
    """Return the process-wide rendering diagnostics store."""
    return _STORE


def _metrics_toggled(_enabled: bool) -> None:
    # Disabled time must never contribute to the next capture interval.
    _STORE.clear()


register_metrics_listener(_metrics_toggled)
