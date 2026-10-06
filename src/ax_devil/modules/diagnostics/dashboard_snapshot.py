"""Collect rendering, source, and cache diagnostics for display and export."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import (
    CacheSnapshot,
    get_frame_cache_registry,
)
from ax_devil.modules.diagnostics.metrics_store import MetricsStore, SourceSnapshot, metrics_enabled
from ax_devil.modules.diagnostics.paint_inspection import InspectionSelection
from ax_devil.modules.diagnostics.render_metrics import ViewerSnapshot, get_render_metrics_store


@dataclass(frozen=True)
class DashboardSnapshot:
    """One dashboard refresh; viewers remain registered while paused."""

    captured_at: float
    enabled: bool
    viewers: tuple[ViewerSnapshot, ...]
    sources: dict[str, SourceSnapshot]
    caches: dict[str, CacheSnapshot]


class DashboardSnapshotService:
    """Supply the same complete diagnostic data to the UI and JSON export."""

    def __init__(self, store: MetricsStore) -> None:
        self._store = store

    def build_snapshot(self) -> DashboardSnapshot:
        """Collect current source state, bounded paint history, and decoded-frame caches."""
        sources = self._store.snapshot()
        enabled = metrics_enabled()
        now = perf_counter()
        return DashboardSnapshot(
            captured_at=now,
            enabled=enabled,
            viewers=get_render_metrics_store().snapshot(now=now),
            sources=sources if enabled else {},
            caches=get_frame_cache_registry().snapshot_details() if enabled else {},
        )

    def build_export_payload(
        self, *, snapshot: DashboardSnapshot | None = None, inspection: InspectionSelection | None = None
    ) -> dict[str, Any]:
        """Export full snapshots, including frame identities, history, and untruncated cache ranges."""
        return {
            "schema_version": 6,
            "inspection": asdict(inspection) if inspection else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "clock": (
                "Paint/submission/source observation times use process monotonic seconds; "
                "frame timestamps are source microseconds."
            ),
            **asdict(snapshot if snapshot is not None else self.build_snapshot()),
        }
