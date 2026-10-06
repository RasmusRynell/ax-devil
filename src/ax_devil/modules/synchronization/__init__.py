"""Synchronization package facade.

Exports the pure-Python sync engine and its Qt adapters.
"""

from __future__ import annotations

from .engine import StreamSync, SyncResult, TimestampedData
from .qt_adapter import QtStreamSync
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
    "QtStreamSync",
    "TimestampFallbackMode",
    "TimestampFallbackPolicy",
    "TimestampMatchResult",
    "TimestampMatchType",
    "count_tolerated_past_overlay_timestamps",
    "find_matching_overlay_timestamp",
]
