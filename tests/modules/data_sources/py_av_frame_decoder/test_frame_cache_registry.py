"""Tests for FrameCacheRegistry rollups and snapshots."""

from __future__ import annotations

import gc

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import (
    CacheSnapshot,
    FrameCache,
    get_frame_cache_registry,
)
from tests.helpers.video import make_cached_frame


def test_frame_cache_registry_tracks_sizes() -> None:
    registry = get_frame_cache_registry()
    baseline = registry.compute_rollup()

    cache = FrameCache(budget_bytes=768 * 3, identifier="test-cache-registry")
    try:
        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "test-cache-registry")
        assert entry["budget_bytes"] == 768 * 3
        assert entry["size"] == 0
        assert entry["min_index"] is None
        assert entry["max_index"] is None
        assert entry["ranges"] == ()

        cache.put(0, make_cached_frame())
        cache.put(1, make_cached_frame())

        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "test-cache-registry")
        assert entry["size"] == 2
        assert entry["min_index"] == 0
        assert entry["max_index"] == 1
        assert entry["ranges"] == ((0, 1),)

        rollup = registry.compute_rollup()
        assert rollup["cache_count"] == baseline["cache_count"] + 1
        assert rollup["total_budget_bytes"] == baseline["total_budget_bytes"] + 768 * 3
        assert rollup["total_frames"] == baseline["total_frames"] + 2

        cache.put(5, make_cached_frame())
        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "test-cache-registry")
        assert entry["ranges"] == ((0, 1), (5, 5))
        assert entry["max_index"] == 5

        cache.clear()
        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "test-cache-registry")
        assert entry["size"] == 0
        assert entry["ranges"] == ()
    finally:
        registry.deregister(cache)
        del cache
        gc.collect()

    assert registry.compute_rollup() == baseline
    assert _get_entry_by_label(registry.snapshot_details(), "test-cache-registry") is None


def test_snapshot_details_supports_duplicate_labels() -> None:
    registry = get_frame_cache_registry()

    cache_a = FrameCache(budget_bytes=768 * 2, identifier="dup-label")
    cache_b = FrameCache(budget_bytes=768 * 2, identifier="dup-label")
    try:
        details = registry.snapshot_details()
        matches = [data for data in details.values() if data.get("label") == "dup-label"]
        assert len(matches) == 2
    finally:
        registry.deregister(cache_a)
        registry.deregister(cache_b)
        del cache_a, cache_b
        gc.collect()


def test_range_insert_merges_adjacent_segments() -> None:
    cache = FrameCache(budget_bytes=768 * 10, identifier="merge-range")
    try:
        for idx in (0, 1, 4, 5, 8, 9):
            cache.put(idx, make_cached_frame())

        registry = get_frame_cache_registry()
        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "merge-range")
        assert entry["ranges"] == ((0, 1), (4, 5), (8, 9))

        # Insert value that extends previous range only
        cache.put(2, make_cached_frame())
        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "merge-range")
        assert entry["ranges"] == ((0, 2), (4, 5), (8, 9))

        # Insert value that bridges previous and next range
        cache.put(3, make_cached_frame())
        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "merge-range")
        assert entry["ranges"] == ((0, 5), (8, 9))

        # Insert value that bridges gap to next range only
        cache.put(7, make_cached_frame())
        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "merge-range")
        assert entry["ranges"] == ((0, 5), (7, 9))

        cache.put(6, make_cached_frame())
        snapshot = registry.snapshot_details()
        entry = _require_entry(snapshot, "merge-range")
        assert entry["ranges"] == ((0, 9),)
    finally:
        registry = get_frame_cache_registry()
        registry.deregister(cache)
        del cache
        gc.collect()


def _get_entry_by_label(snapshot: dict[str, CacheSnapshot], label: str) -> CacheSnapshot | None:
    for data in snapshot.values():
        if data.get("label") == label:
            return data
    return None


def _require_entry(snapshot: dict[str, CacheSnapshot], label: str) -> CacheSnapshot:
    entry = _get_entry_by_label(snapshot, label)
    if entry is None:
        raise AssertionError(f"Missing snapshot entry for label '{label}'")
    return entry


def test_eviction_preserves_recent_frames_and_updates_ranges() -> None:
    """Reading refreshes LRU order, replacement keeps capacity, and eviction splits ranges."""
    cache = FrameCache(budget_bytes=768 * 3, identifier="eviction")
    registry = get_frame_cache_registry()
    try:
        for index in range(3):
            cache.put(index, make_cached_frame(index))
        assert cache.get(0) is not None
        cache.put(3, make_cached_frame(3))
        assert cache.get(1) is None
        assert cache.get(0) is not None
        assert cache.get(2) is not None
        assert cache.get(3) is not None
        assert _require_entry(registry.snapshot_details(), "eviction")["ranges"] == ((0, 0), (2, 3))

        replacement = make_cached_frame(2)
        cache.put(2, replacement)
        assert cache.size() == 3
        assert cache.get(2) is replacement
        cache.put(4, make_cached_frame(4))
        assert cache.get(0) is None
        assert _require_entry(registry.snapshot_details(), "eviction")["ranges"] == ((2, 4),)
    finally:
        registry.deregister(cache)
