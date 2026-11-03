"""The one way cancellable work learns it should stop: it polls a `CancelToken`, which raises `Cancelled`.

Whoever starts work owns its token and calls `cancel()`; the work only polls. Standard library only, so any layer may
use it.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from typing import TypeVar

T = TypeVar("T")


class Cancelled(BaseException):
    """Raised inside work whose token was cancelled.

    A `BaseException`, like `KeyboardInterrupt`, so an ``except Exception`` that logs or converts failures can never
    swallow it. Clean up with ``finally``; catch it only where the work was started, to stop quietly.
    """


class CancelToken:
    """A request to stop, shared by an owner and the work it started.

    The owner calls `cancel()`, which is safe from any thread. The work never cancels its own token; it checks it
    between operations with `raise_if_cancelled`, `polled` or `wait`. There is deliberately no way to ask whether the
    token is cancelled: work stops by being interrupted, not by branching on a flag.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Ask the work to stop. Only the owner calls this; it never waits."""
        self._event.set()

    def raise_if_cancelled(self) -> None:
        """Raise `Cancelled` if the token was cancelled; call it between steps of long work."""
        if self._event.is_set():
            raise Cancelled

    def polled(self, items: Iterable[T]) -> Iterator[T]:
        """Yield *items*, raising `Cancelled` before each one once the token is cancelled."""
        for item in items:
            self.raise_if_cancelled()
            yield item

    def wait(self, seconds: float) -> None:
        """Sleep up to *seconds*, raising `Cancelled` at once if the token is cancelled meanwhile."""
        if self._event.wait(seconds):
            raise Cancelled


class _NeverCancelled(CancelToken):
    """A token nobody can cancel."""

    def cancel(self) -> None:
        """Refuse: the token is shared by every caller that has nothing to cancel."""
        raise RuntimeError("NEVER cannot be cancelled; create a CancelToken to cancel work.")


NEVER: CancelToken = _NeverCancelled()
"""For callers with nothing to cancel, such as the command line and tests: polling it never raises, and `wait` sleeps
the full time."""
