"""Media time is integer microseconds on the clock frames and Scenes share."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fractions import Fraction

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_US_PER_SECOND = 1_000_000
_US_PER_MS = 1000


def seconds_to_us(seconds: float | Fraction) -> int:
    """Convert seconds to integer microseconds, rounding ties to even."""
    return round(seconds * _US_PER_SECOND)


def ms_to_us(milliseconds: float) -> int:
    """Convert milliseconds to integer microseconds, rounding ties to even."""
    return round(milliseconds * _US_PER_MS)


def ns_to_us(nanoseconds: int) -> int:
    """Convert nanoseconds to integer microseconds exactly, rounding ties to even."""
    return round(Fraction(nanoseconds, 1000))


def us_to_seconds(timestamp_us: int) -> float:
    """Convert integer microseconds to seconds."""
    return timestamp_us / _US_PER_SECOND


def us_to_ms(timestamp_us: int) -> float:
    """Convert integer microseconds to milliseconds."""
    return timestamp_us / _US_PER_MS


def seconds_to_ms(seconds: float) -> float:
    """Convert seconds to milliseconds."""
    return seconds * 1000


def ms_to_seconds(milliseconds: float) -> float:
    """Convert milliseconds to seconds."""
    return milliseconds / 1000


def datetime_to_us(moment: datetime) -> int:
    """Convert a timezone-aware datetime to integer microseconds since the Unix epoch."""
    if moment.utcoffset() is None:
        raise ValueError("datetime_to_us needs a timezone-aware datetime")
    return (moment - _EPOCH) // timedelta(microseconds=1)


def us_to_datetime(timestamp_us: int) -> datetime:
    """Convert integer microseconds since the Unix epoch to a UTC datetime."""
    return _EPOCH + timedelta(microseconds=timestamp_us)
