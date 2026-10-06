"""Typed source observations with independent monotonic freshness."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from time import perf_counter

from ax_devil.modules.diagnostics.metrics_gate import is_metrics_enabled, register_metrics_listener
from ax_devil.modules.diagnostics.metrics_gate import set_metrics_enabled as _set_metrics_enabled

MetricValue = str | int | float | None


def source_identity(owner: object) -> str:
    """Return an instance identity independent of a source's display name."""
    return f"source:{id(owner):x}"


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """One latest value and the monotonic time when that value was observed."""

    value: MetricValue
    observed_at: float


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """A labelled source whose fields may describe different frames and times."""

    label: str
    observations: dict[str, SourceObservation] = field(default_factory=dict)


class MetricsStore:
    """Retain source lifetimes and individually timestamped observations."""

    def __init__(self) -> None:
        self._data: dict[str, SourceSnapshot] = {}
        self._lock = RLock()

    def register(self, instance_id: str, label: str) -> None:
        """Start a source lifetime, replacing observations from a previously reused identity."""
        with self._lock:
            self._data[instance_id] = SourceSnapshot(label)

    def set_metric(self, instance_id: str, key: str, value: MetricValue) -> None:
        """Record a value with its own observation time."""
        with self._lock:
            entry = self._data.setdefault(instance_id, SourceSnapshot(instance_id))
            entry.observations[key] = SourceObservation(value, perf_counter())

    def remove_instance(self, instance_id: str) -> None:
        """Release a closed source and its observations."""
        with self._lock:
            self._data.pop(instance_id, None)

    def clear(self) -> None:
        """Clear observations while preserving source labels and identity."""
        with self._lock:
            self._data = {key: SourceSnapshot(value.label) for key, value in self._data.items()}

    def snapshot(self, instance_ids: tuple[str, ...] | None = None) -> dict[str, SourceSnapshot]:
        """Copy latest observations without claiming atomic frame coherence."""
        with self._lock:
            keys = self._data.keys() if instance_ids is None else instance_ids
            return {
                key: SourceSnapshot(value.label, dict(value.observations))
                for key in keys
                if (value := self._data.get(key)) is not None
            }


_STORE = MetricsStore()


def _handle_metrics_toggle(enabled: bool) -> None:
    if not enabled:
        _STORE.clear()


register_metrics_listener(_handle_metrics_toggle)


def get_metrics_store() -> MetricsStore:
    """Return the process-wide source store."""
    return _STORE


def set_metric(instance_id: str, key: str, value: MetricValue) -> None:
    """Publish a source observation when capture is enabled."""
    if is_metrics_enabled():
        _STORE.set_metric(instance_id, key, value)


def remove_instance(instance_id: str) -> None:
    """Remove a source when its owner closes."""
    _STORE.remove_instance(instance_id)


def metrics_enabled() -> bool:
    """Return whether diagnostic capture is active."""
    return is_metrics_enabled()


def set_metrics_enabled(enabled: bool) -> None:
    """Enable or disable diagnostic capture globally."""
    _set_metrics_enabled(enabled)
