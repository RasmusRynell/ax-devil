"""One owner's replaceable interest in a worker or service request."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from PySide6.QtCore import QObject

from ax_devil.modules.background_jobs.jobs import JobContext, JobScope

R = TypeVar("R")
P = TypeVar("P")
Cancel = Callable[[], None]


def _nothing() -> None:
    """Cancel an interest that has already ended."""


class _Request(Generic[R, P]):
    """Callbacks that can be released even when a service retains its delivery functions."""

    def __init__(
        self,
        slot: LatestRequest,
        on_result: Callable[[R], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[P], None] | None,
    ) -> None:
        self.slot = slot
        self.on_result: Callable[[R], None] | None = on_result
        self.on_error: Callable[[Exception], None] | None = on_error
        self.on_progress = on_progress
        self.cancel_operation: Cancel | None = None

    def cancel(self) -> None:
        """Release consumer callbacks before cancelling an attached operation."""
        cancel = self.cancel_operation
        self._clear()
        if cancel is not None:
            cancel()

    def succeed(self, result: R) -> None:
        """Finish before delivering a current result, allowing reentrant replacement."""
        callback = self.on_result
        if self.slot._current is self and callback is not None:
            self._clear()
            callback(result)

    def fail(self, error: Exception) -> None:
        """Finish before delivering a current error, allowing a retry from the callback."""
        callback = self.on_error
        if self.slot._current is self and callback is not None:
            self._clear()
            callback(error)

    def progress(self, value: P) -> None:
        """Deliver progress only while this request is current."""
        if self.slot._current is self and self.on_progress is not None:
            self.on_progress(value)

    def _clear(self) -> None:
        if self.slot._current is self:
            self.slot._current = None
        self.on_result = self.on_error = self.on_progress = None
        self.cancel_operation = None


class LatestRequest:
    """Deliver only the latest request's outcome and progress, for one GUI-thread owner.

    Replacement and cancellation release the consumer callbacks immediately and never wait for work. Completed
    results belong to the consumer: it decides whether changing inputs clears them or keeps the last good output.
    Call ``close`` during owner cleanup; destruction also closes this slot. All methods run on the GUI thread.
    """

    def __init__(self, owner: QObject) -> None:
        self._current: _Request[Any, Any] | None = None
        self._closed = False
        owner.destroyed.connect(self.close)

    @property
    def pending(self) -> bool:
        """Return whether the current request still awaits an outcome."""
        return self._current is not None

    def cancel(self) -> None:
        """Invalidate pending delivery and cancel its operation, allowing another request later."""
        if self._current is not None:
            self._current.cancel()

    def close(self) -> None:
        """Cancel pending delivery and reject future requests, without waiting for work."""
        self._closed = True
        self.cancel()

    def start(
        self,
        request: Callable[[Callable[[R], None], Callable[[Exception], None]], Cancel],
        *,
        on_result: Callable[[R], None],
        on_error: Callable[[Exception], None],
    ) -> Cancel:
        """Replace the current request with a service that accepts result and error callbacks.

        The service may deliver synchronously, including starting another request from its callback. Pending state
        ends before a completion callback runs. Return cancellation of this particular request, not its successor.
        """
        return self._start(lambda state: request(state.succeed, state.fail), on_result, on_error, None)

    def submit(
        self,
        scope: JobScope,
        name: str,
        work: Callable[[JobContext[P]], R],
        *,
        on_result: Callable[[R], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[P], None] | None = None,
        on_discard: Callable[[R], None] | None = None,
    ) -> Cancel:
        """Replace the current request with a job; progress and discarded resources follow ``JobScope.submit``.

        Close this slot before closing its scope during owner cleanup, so pending state ends along with delivery.
        """
        return self._start(
            lambda state: scope.submit(
                name,
                work,
                on_result=state.succeed,
                on_error=state.fail,
                on_progress=state.progress if on_progress is not None else None,
                on_discard=on_discard,
            ),
            on_result,
            on_error,
            on_progress,
        )

    def _start(
        self,
        start: Callable[[_Request[R, P]], Cancel],
        on_result: Callable[[R], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[P], None] | None,
    ) -> Cancel:
        if self._closed:
            return _nothing
        previous = self._current
        state = _Request(self, on_result, on_error, on_progress)
        self._current = state
        if previous is not None:
            previous.cancel()
        # Cancellation can itself call consumer code that replaces or closes this request.
        if self._current is not state:
            return state.cancel
        try:
            cancel = start(state)
        except BaseException:
            state.cancel()
            raise
        if self._current is state:
            state.cancel_operation = cancel
        else:
            # A cached completion or reentrant cancellation ran before the operation supplied its handle.
            cancel()
        return state.cancel
