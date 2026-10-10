"""The diagnostic export includes everything the dashboard can inspect."""

import json

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import (
    FrameCache,
    get_frame_cache_registry,
)
from ax_devil.modules.diagnostics.dashboard_snapshot import DashboardSnapshotService
from ax_devil.modules.diagnostics.metrics_store import MetricsStore, set_metrics_enabled
from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store
from tests.helpers.video import make_cached_frame


def test_snapshot_and_export_include_viewers_sources_and_full_cache_ranges() -> None:
    set_metrics_enabled(True)
    store = MetricsStore()
    service = DashboardSnapshotService(store)
    render = get_render_metrics_store()
    render.register("export-test", "Camera A")
    store.set_metric("Source A", "Overlay offset (ms)", None)
    cache = FrameCache(budget_bytes=100 * 768, identifier="A")
    try:
        for index in range(0, 100, 2):
            cache.put(index, make_cached_frame(index))
        snapshot = service.build_snapshot()
        assert any(viewer.viewer_id == "export-test" for viewer in snapshot.viewers)
        assert snapshot.sources["Source A"].observations["Overlay offset (ms)"].value is None
        detail = next(value for value in snapshot.caches.values() if value["label"] == "A")
        assert len(detail["ranges"]) == 50
        assert detail["reserved_bytes"] == 50 * 768
        assert detail["budget_bytes"] == 100 * 768
        payload = service.build_export_payload()
        assert payload["caches"]
        assert payload["viewers"]
        assert (
            json.loads(json.dumps(payload))["sources"]["Source A"]["observations"]["Overlay offset (ms)"]["value"]
            is None
        )
    finally:
        render.remove("export-test")
        get_frame_cache_registry().deregister(cache)


def test_disabled_metrics_hide_source_and_cache_values() -> None:
    store = MetricsStore()
    store.set_metric("A", "value", 3)
    set_metrics_enabled(False)
    try:
        snapshot = DashboardSnapshotService(store).build_snapshot()
        assert not snapshot.enabled
        assert not snapshot.sources
        assert not snapshot.caches
    finally:
        set_metrics_enabled(True)


def test_source_freshness_is_per_field_and_frozen_exports_do_not_read_live_data() -> None:
    """Updating one observation must not make an older field appear fresh."""
    from unittest.mock import patch

    store = MetricsStore()
    store.register("same-name-a", "Camera")
    store.register("same-name-b", "Camera")
    with patch("ax_devil.modules.diagnostics.metrics_store.perf_counter", return_value=10.0):
        store.set_metric("same-name-a", "Decoded", "#1")
    with patch("ax_devil.modules.diagnostics.metrics_store.perf_counter", return_value=20.0):
        store.set_metric("same-name-a", "Synced", "#2")
    service = DashboardSnapshotService(store)
    frozen = service.build_snapshot()
    store.set_metric("same-name-a", "Decoded", "#3")
    payload = service.build_export_payload(snapshot=frozen)
    observations = payload["sources"]["same-name-a"]["observations"]
    assert observations["Decoded"] == {"value": "#1", "observed_at": 10.0}
    assert observations["Synced"]["observed_at"] == 20.0
    assert payload["sources"]["same-name-b"]["observations"] == {}
    store.clear()
    assert store.snapshot()["same-name-a"].label == "Camera"
    assert not store.snapshot()["same-name-a"].observations
    store.set_metric("same-name-a", "Old field", 1)
    store.register("same-name-a", "Replacement source")
    assert not store.snapshot()["same-name-a"].observations
