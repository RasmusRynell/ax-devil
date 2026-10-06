"""Distribute one memory allowance across the active decoded-frame caches."""

from __future__ import annotations

import threading
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import FrameCache


class FrameCachePool:
    """Share a byte budget equally, shrinking caches before growing any share.

    Pool operations acquire the pool lock before cache locks. Cache code must
    release its own lock before joining or leaving this pool. Weak references
    backstop explicit decoder cleanup without retaining abandoned sources.
    """

    def __init__(self, budget_bytes: int) -> None:
        if budget_bytes < 0:
            raise ValueError("Shared frame cache budget cannot be negative")
        self._budget_bytes = budget_bytes
        self._caches: dict[int, weakref.ReferenceType[FrameCache]] = {}
        self._lock = threading.RLock()

    def register(self, cache: FrameCache) -> None:
        """Add a newly opened source and redistribute the shared allowance."""
        with self._lock:
            self._caches[id(cache)] = weakref.ref(cache, lambda _, key=id(cache): self._remove(key))
            self._rebalance()

    def deregister(self, cache: FrameCache) -> None:
        """Remove a closed cache after it has discarded its retained frames."""
        self._remove(id(cache))

    def set_budget_bytes(self, budget_bytes: int) -> None:
        """Apply an updated total allowance to all open sources immediately."""
        if budget_bytes < 0:
            raise ValueError("Shared frame cache budget cannot be negative")
        with self._lock:
            self._budget_bytes = budget_bytes
            self._rebalance()

    @property
    def source_count(self) -> int:
        """Return the number of registered sources, including paused sources."""
        with self._lock:
            return len(self._caches)

    def _remove(self, key: int) -> None:
        with self._lock:
            self._caches.pop(key, None)
            self._rebalance()

    def _rebalance(self) -> None:
        caches = [cache for ref in self._caches.values() if (cache := ref()) is not None]
        if not caches:
            return
        share = self._budget_bytes // len(caches)
        # Never grow a share until every shrinking cache has released its excess.
        for cache in caches:
            cache.set_budget_bytes(min(cache.budget_bytes, share))
        for cache in caches:
            cache.set_budget_bytes(share)
