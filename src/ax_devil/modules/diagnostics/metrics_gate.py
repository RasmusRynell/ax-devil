"""Global toggle for diagnostic metrics collection."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_METRICS_ENABLED = True
_LOCK = RLock()
_LISTENERS: list[Callable[[bool], None]] = []


def is_metrics_enabled() -> bool:
    """Return True when diagnostics metrics capture is enabled."""
    with _LOCK:
        return _METRICS_ENABLED


def set_metrics_enabled(enabled: bool) -> None:
    """Toggle diagnostics metrics capture and notify listeners."""
    global _METRICS_ENABLED
    with _LOCK:
        if _METRICS_ENABLED == enabled:
            return
        _METRICS_ENABLED = enabled
        listeners = list(_LISTENERS)

    for callback in listeners:
        try:
            callback(enabled)
        except Exception:
            logger.exception("Metrics toggle listener failed")


def register_metrics_listener(callback: Callable[[bool], None]) -> None:
    """Register a listener that runs whenever the global toggle changes."""
    with _LOCK:
        _LISTENERS.append(callback)
