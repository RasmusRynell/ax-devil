"""A cancel token is how any work, whatever it is, learns it should stop."""

from __future__ import annotations

import threading
import time

import pytest

from ax_devil.core.cancellation import NEVER, Cancelled, CancelToken


def test_polling_a_cancelled_token_raises_and_an_untouched_one_does_not() -> None:
    token = CancelToken()
    token.raise_if_cancelled()

    token.cancel()

    with pytest.raises(Cancelled):
        token.raise_if_cancelled()


def test_polled_yields_every_item_until_cancelled_and_then_stops_before_the_next() -> None:
    token = CancelToken()
    seen: list[int] = []

    assert list(token.polled(range(3))) == [0, 1, 2]
    with pytest.raises(Cancelled):
        for item in token.polled(range(10)):
            seen.append(item)
            if item == 2:
                token.cancel()

    assert seen == [0, 1, 2], "cancelling during an item lets that item finish and skips the rest"


def test_wait_returns_after_its_time_and_wakes_at_once_when_cancelled() -> None:
    token = CancelToken()
    token.wait(0)
    threading.Timer(0.1, token.cancel).start()
    started = time.monotonic()

    with pytest.raises(Cancelled):
        token.wait(30)

    assert time.monotonic() - started < 5


def test_cancellation_is_not_an_exception_so_a_broad_except_cannot_swallow_it() -> None:
    token = CancelToken()
    token.cancel()
    swallowed: list[Exception] = []

    with pytest.raises(Cancelled):
        try:
            token.raise_if_cancelled()
        except Exception as exc:
            swallowed.append(exc)

    assert swallowed == []


def test_the_token_for_callers_with_nothing_to_cancel_never_cancels() -> None:
    NEVER.raise_if_cancelled()
    assert list(NEVER.polled("ab")) == ["a", "b"]
    NEVER.wait(0)
    with pytest.raises(RuntimeError):
        NEVER.cancel()
    NEVER.raise_if_cancelled()
