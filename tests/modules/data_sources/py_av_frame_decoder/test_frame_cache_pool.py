"""Shared cache shares resize safely during source lifecycle and concurrent reads."""

import gc
import threading
from concurrent.futures import ThreadPoolExecutor

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import (
    FrameCache,
    get_frame_cache_registry,
)
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache_pool import FrameCachePool
from tests.helpers.video import make_cached_frame


def test_join_leave_and_resize_evict_immediately() -> None:
    pool = FrameCachePool(6 * 768)
    first = FrameCache(0, pool=pool)
    try:
        for index in range(6):
            assert first.put(index, make_cached_frame(index))
        second = FrameCache(0, pool=pool)
        try:
            assert first.budget_bytes == second.budget_bytes == 3 * 768
            assert first.size() == 3
            assert not first.contains(0)
            for index in range(3):
                assert second.put(index, make_cached_frame(index))
            pool.set_budget_bytes(2 * 768)
            assert first.size() == second.size() == 1
            assert first.contains(5) and second.contains(2)
            snapshot = get_frame_cache_registry().snapshot_details()
            assert sum(snapshot[str(id(cache))]["reserved_bytes"] for cache in (first, second)) == 2 * 768
            assert all(snapshot[str(id(cache))]["budget_bytes"] == 768 for cache in (first, second))
        finally:
            second.close()
        assert pool.source_count == 1
        assert first.budget_bytes == 2 * 768
        assert first.put(6, make_cached_frame(6))
        second.close()  # Idempotent shutdown must not redistribute twice.
        assert pool.source_count == 1
    finally:
        first.close()
    assert pool.source_count == 0
    assert not first.put(7, make_cached_frame(7))


def test_zero_share_and_abandoned_source() -> None:
    pool = FrameCachePool(1)
    first = FrameCache(0, pool=pool)
    second = FrameCache(0, pool=pool)
    assert first.budget_bytes == second.budget_bytes == 0
    assert not first.put(0, make_cached_frame())
    del second
    gc.collect()
    assert pool.source_count == 1
    assert first.budget_bytes == 1
    pool.set_budget_bytes(0)
    assert first.budget_bytes == 0
    first.close()


def test_concurrent_resizing_and_cache_access_finish_without_deadlock() -> None:
    pool = FrameCachePool(8 * 768)
    caches = [FrameCache(0, pool=pool) for _ in range(2)]
    start = threading.Barrier(3)

    def read_and_write(cache: FrameCache) -> None:
        start.wait(timeout=5)
        for index in range(150):
            cache.put(index, make_cached_frame(index))
            cache.get(index)
            cache.can_admit(index + 1, 768, range(index, index + 2))
        cache.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(read_and_write, cache) for cache in caches]
            start.wait(timeout=5)
            for index in range(150):
                pool.set_budget_bytes((index % 9) * 768)
            for future in futures:
                future.result(timeout=5)
        assert pool.source_count == 0
        assert all(cache.size() == 0 for cache in caches)
    finally:
        for cache in caches:
            cache.close()
