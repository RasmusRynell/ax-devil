"""Bind the application's shared video cache pool to its playback preference."""

import threading

from PySide6.QtCore import Qt

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache_pool import FrameCachePool
from ax_devil.modules.settings.playback_settings import VideoCacheBudget
from ax_devil.modules.settings.settings import GlobalSettings

_pool: FrameCachePool | None = None
_pool_settings: GlobalSettings | None = None
_lock = threading.RLock()


def get_video_cache_pool() -> FrameCachePool:
    """Return the process pool and bind preference updates once per settings instance."""
    global _pool, _pool_settings
    with _lock:
        settings = GlobalSettings()
        if _pool is None or _pool_settings is not settings:
            pool = FrameCachePool(0)
            available_memory_bytes = settings.available_memory_bytes

            def apply_budget(budget: VideoCacheBudget) -> None:
                with _lock:
                    pool.set_budget_bytes(budget.resolve_bytes(available_memory_bytes))

            # Source construction can run on a loader thread without a Qt event loop.
            # The pool uses locks and touches no widgets, so direct delivery is safe.
            settings.video_cache_budget_changed.connect(apply_budget, Qt.ConnectionType.DirectConnection)
            apply_budget(settings.video_cache_budget)
            _pool_settings = settings
            _pool = pool
        return _pool
