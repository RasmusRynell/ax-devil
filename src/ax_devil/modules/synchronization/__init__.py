"""Synchronization package facade.

Exports the pure-Python sync engine; the Qt adapter lives in ``qt_adapter`` and is imported from there.
"""

from __future__ import annotations

from .engine import StreamSync, SyncResult, TimestampedData
from .timestamp_matching import (
    TimestampFallbackMode,
    TimestampFallbackPolicy,
    TimestampMatchResult,
    TimestampMatchType,
    count_tolerated_past_overlay_timestamps,
    find_matching_overlay_timestamp,
)

__all__ = [
    "StreamSync",
    "SyncResult",
    "TimestampedData",
    "TimestampFallbackMode",
    "TimestampFallbackPolicy",
    "TimestampMatchResult",
    "TimestampMatchType",
    "count_tolerated_past_overlay_timestamps",
    "find_matching_overlay_timestamp",
]
