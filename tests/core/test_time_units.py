"""Media time converts between clock units without losing microseconds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest

from ax_devil.core.time_units import datetime_to_us, ns_to_us, seconds_to_us, us_to_datetime


def test_an_aware_datetime_round_trips_through_microseconds_exactly() -> None:
    moment = datetime(2026, 10, 18, 12, 0, 0, 123_456, tzinfo=timezone(timedelta(hours=2)))
    assert us_to_datetime(datetime_to_us(moment)) == moment
    assert datetime_to_us(moment) == datetime_to_us(moment.astimezone(timezone.utc))


def test_a_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError):
        datetime_to_us(datetime(2026, 10, 18, 12, 0, 0))


def test_nanoseconds_and_fractional_seconds_round_to_the_nearest_microsecond() -> None:
    assert ns_to_us(1_700_000_000_123_456_789) == 1_700_000_000_123_457
    assert seconds_to_us(Fraction(2, 30)) == 66_667
