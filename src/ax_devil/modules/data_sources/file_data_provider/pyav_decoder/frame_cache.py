"""Thread-safe LRU cache for video frames."""

import bisect
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.cached_frame import CachedFrame
from ax_devil.modules.settings.logging_config import get_logger

if TYPE_CHECKING:
    from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache_pool import FrameCachePool


class FrameCache:
    """Thread-safe LRU cache for video frames."""

    def __init__(
        self, budget_bytes: int, *, identifier: str | None = None, pool: "FrameCachePool | None" = None
    ) -> None:
        if budget_bytes < 0:
            raise ValueError("Frame cache byte budget cannot be negative")
        self.budget_bytes = budget_bytes if pool is None else 0
        self._pool = pool
        self._closed = False
        self._reserved_bytes = 0
        self._cache: OrderedDict[int, CachedFrame] = OrderedDict()
        self._lock = threading.RLock()
        self._ranges: list[list[int]] = []
        self.identifier = identifier or f"FrameCache-{id(self)}"

        self.logger = get_logger(__name__)
        self.logger.debug(
            f"FrameCache created: id={id(self)}, budget_bytes={budget_bytes}, identifier={self.identifier}"
        )
        if pool is not None:
            pool.register(self)

    def __del__(self) -> None:
        """Release the cache's memory and registrations when it is garbage collected."""
        try:
            self.close()
        except Exception:
            self.logger.debug(f"FrameCache id={id(self)} failed to close on garbage collection", exc_info=True)

    def set_budget_bytes(self, budget_bytes: int) -> None:
        """Resize a live cache and evict excess history under the cache lock."""
        if budget_bytes < 0:
            raise ValueError("Frame cache byte budget cannot be negative")
        with self._lock:
            if self._closed or budget_bytes == self.budget_bytes:
                return
            self.budget_bytes = budget_bytes
            while self._reserved_bytes > budget_bytes:
                index, frame = self._cache.popitem(last=False)
                self._reserved_bytes -= frame.reserved_bytes
                self._remove_from_ranges_locked(index)

    def close(self) -> None:
        """Release retained frames and return this source's share to the pool."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.budget_bytes = 0
            self.clear()
        # Pool rebalancing takes other cache locks: never enter it with ours held.
        if self._pool is not None:
            self._pool.deregister(self)

    def get(self, frame_index: int) -> CachedFrame | None:
        """Get frame by index, returns None if not cached."""
        with self._lock:
            if frame_index in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(frame_index)
                return self._cache[frame_index]

            return None

    def can_admit(self, frame_index: int, reserved_bytes: int, keep: range) -> bool:
        """Check that *frame_index* fits without evicting any other frame in *keep*."""
        with self._lock:
            return self._kept_bytes_locked(frame_index, keep) + reserved_bytes <= self.budget_bytes

    def put(self, frame_index: int, frame_data: CachedFrame, *, keep: range = range(0)) -> bool:
        """Admit within budget without evicting any other frame in *keep*.

        Oversized frames are delivered by the reader without caching them.
        A frame that does not fit beside *keep* is rejected and leaves existing entries unchanged.
        """
        with self._lock:
            if self._closed or frame_data.reserved_bytes > self.budget_bytes:
                return False
            if not self.can_admit(frame_index, frame_data.reserved_bytes, keep):
                return False
            previous = self._cache.pop(frame_index, None)
            if previous is not None:
                self._reserved_bytes -= previous.reserved_bytes
                self._remove_from_ranges_locked(frame_index)
            for index in list(self._cache):
                if self._reserved_bytes + frame_data.reserved_bytes <= self.budget_bytes:
                    break
                if index in keep:
                    continue
                evicted = self._cache.pop(index)
                self._reserved_bytes -= evicted.reserved_bytes
                self._remove_from_ranges_locked(index)
            self._cache[frame_index] = frame_data
            self._reserved_bytes += frame_data.reserved_bytes
            self._add_to_ranges_locked(frame_index)
            return True

    def contains(self, frame_index: int) -> bool:
        """Check if frame is cached without affecting LRU order."""
        with self._lock:
            return frame_index in self._cache

    def clear(self) -> None:
        """Clear all cached frames."""
        with self._lock:
            frames_cleared = len(self._cache)
            self._cache.clear()
            self._reserved_bytes = 0
            self.logger.debug(f"FrameCache cleared: id={id(self)}, cleared {frames_cleared} frames")
            self._ranges.clear()

    def size(self) -> int:
        """Get current cache size."""
        with self._lock:
            return len(self._cache)

    def cached_ranges(self) -> tuple[tuple[int, int], ...]:
        """Return the cached frame indices as sorted, inclusive ``(first, last)`` runs."""
        with self._lock:
            return tuple((start, end) for start, end in self._ranges)

    def _kept_bytes_locked(self, frame_index: int, keep: range) -> int:
        return sum(
            frame.reserved_bytes for index, frame in self._cache.items() if index in keep and index != frame_index
        )

    def _add_to_ranges_locked(self, frame_index: int) -> None:
        """Insert *frame_index* into the range list in O(log n)."""
        if not self._ranges:
            self._ranges.append([frame_index, frame_index])
            return

        starts = [r[0] for r in self._ranges]
        pos = bisect.bisect_right(starts, frame_index)

        merges_prev = pos > 0 and self._ranges[pos - 1][1] + 1 == frame_index
        merges_next = pos < len(self._ranges) and self._ranges[pos][0] - 1 == frame_index

        if merges_prev and merges_next:
            self._ranges[pos - 1][1] = self._ranges[pos][1]
            del self._ranges[pos]
        elif merges_prev:
            self._ranges[pos - 1][1] = frame_index
        elif merges_next:
            self._ranges[pos][0] = frame_index
        else:
            self._ranges.insert(pos, [frame_index, frame_index])

    def _remove_from_ranges_locked(self, frame_index: int) -> None:
        """Remove *frame_index* from the range list in O(log n)."""
        starts = [r[0] for r in self._ranges]
        pos = bisect.bisect_right(starts, frame_index) - 1
        if pos < 0:
            return

        r = self._ranges[pos]
        if frame_index < r[0] or frame_index > r[1]:
            return

        if r[0] == r[1]:
            del self._ranges[pos]
        elif frame_index == r[0]:
            r[0] += 1
        elif frame_index == r[1]:
            r[1] -= 1
        else:
            new_right = [frame_index + 1, r[1]]
            r[1] = frame_index - 1
            self._ranges.insert(pos + 1, new_right)
