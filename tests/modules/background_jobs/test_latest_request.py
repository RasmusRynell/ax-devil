"""Replacing requests preserves only current delivery, including synchronous service callbacks."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from pytestqt.qtbot import QtBot

from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, LatestRequest


class _ServiceRequest:
    """A service retaining its callbacks even after cancellation, as an in-flight transport might."""

    def __init__(self) -> None:
        self.result: Callable[[str], None] | None = None
        self.error: Callable[[Exception], None] | None = None
        self.cancelled = False

    def start(self, result: Callable[[str], None], error: Callable[[Exception], None]) -> Callable[[], None]:
        """Hold delivery until the test completes the request."""
        self.result, self.error = result, error
        return self.cancel

    def cancel(self) -> None:
        """Record cancellation without relinquishing queued callbacks."""
        self.cancelled = True

    def succeed(self, value: str) -> None:
        """Deliver a result, including an already queued result after cancellation."""
        assert self.result is not None
        self.result(value)

    def fail(self, message: str) -> None:
        """Deliver an error, including an already queued error after cancellation."""
        assert self.error is not None
        self.error(OSError(message))


def test_replacement_and_old_cancel_handles_leave_only_the_latest_delivery() -> None:
    owner = QObject()
    slot = LatestRequest(owner)
    first, second = _ServiceRequest(), _ServiceRequest()
    results: list[str] = []
    errors: list[Exception] = []
    cancel_first = slot.start(first.start, on_result=results.append, on_error=errors.append)
    slot.start(second.start, on_result=results.append, on_error=errors.append)
    cancel_first()
    first.succeed("obsolete")
    first.fail("obsolete failure")

    assert slot.pending
    assert first.cancelled and not second.cancelled
    second.succeed("current")
    second.fail("duplicate completion")
    assert results == ["current"]
    assert errors == []
    assert not slot.pending


def test_cached_completion_can_start_a_replacement_that_cleanup_still_cancels() -> None:
    owner = QObject()
    slot = LatestRequest(owner)
    cached, replacement = _ServiceRequest(), _ServiceRequest()
    results: list[str] = []
    errors: list[Exception] = []

    def request_cached(result: Callable[[str], None], error: Callable[[Exception], None]) -> Callable[[], None]:
        cancel = cached.start(result, error)
        result("cached")
        return cancel

    def receive_cached(value: str) -> None:
        assert not slot.pending
        results.append(value)
        slot.start(replacement.start, on_result=results.append, on_error=errors.append)

    cancel_cached = slot.start(request_cached, on_result=receive_cached, on_error=errors.append)
    cancel_cached()
    assert slot.pending
    assert not replacement.cancelled
    slot.close()
    replacement.succeed("too late")

    assert replacement.cancelled
    assert not slot.pending
    assert results == ["cached"]
    assert errors == []


def test_cancel_before_the_service_returns_its_handle_still_stops_the_operation() -> None:
    owner = QObject()
    slot = LatestRequest(owner)
    service = _ServiceRequest()
    delivered: list[object] = []

    def request(result: Callable[[str], None], error: Callable[[Exception], None]) -> Callable[[], None]:
        cancel = service.start(result, error)
        slot.cancel()
        return cancel

    slot.start(request, on_result=delivered.append, on_error=delivered.append)
    service.succeed("too late")

    assert service.cancelled
    assert not slot.pending
    assert delivered == []


@pytest.mark.parametrize("end", ["close", "destroy owner"])
def test_owner_end_cancels_delivery_and_prevents_new_service_requests(qtbot: QtBot, end: str) -> None:
    owner = QObject()
    slot = LatestRequest(owner)
    service, later = _ServiceRequest(), _ServiceRequest()
    delivered: list[object] = []
    slot.start(service.start, on_result=delivered.append, on_error=delivered.append)

    if end == "close":
        slot.close()
    else:
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    slot.start(later.start, on_result=delivered.append, on_error=delivered.append)
    service.succeed("too late")
    service.fail("too late")

    assert service.cancelled
    assert later.result is None
    assert not slot.pending
    assert delivered == []


def test_cancellation_releases_consumer_callbacks_while_service_still_holds_delivery() -> None:
    class Receiver:
        """A controller held only by its completion callbacks."""

        def receive(self, value: object) -> None:
            """Accept a service result or error."""

    owner = QObject()
    slot = LatestRequest(owner)
    service = _ServiceRequest()
    receiver = Receiver()
    reference = weakref.ref(receiver)
    slot.start(service.start, on_result=receiver.receive, on_error=receiver.receive)
    del receiver
    assert reference() is not None

    slot.cancel()

    assert reference() is None
    service.succeed("queued result")


def test_replaced_worker_discards_its_resource_and_queued_progress(qtbot: QtBot) -> None:
    owner = QObject()
    jobs = BackgroundJobs(max_workers=1)
    scope = jobs.scope(owner)
    slot = LatestRequest(owner)
    reported, release = threading.Event(), threading.Event()
    outcomes: list[object] = []
    discarded: list[str] = []

    def first(job: JobContext[str]) -> str:
        job.report("obsolete progress")
        reported.set()
        assert release.wait(5)
        return "unwanted resource"

    def second(job: JobContext[str]) -> str:
        job.report("current progress")
        return "current result"

    slot.submit(
        scope,
        "first",
        first,
        on_result=outcomes.append,
        on_error=outcomes.append,
        on_progress=outcomes.append,
        on_discard=discarded.append,
    )
    try:
        assert reported.wait(5)
        slot.submit(
            scope,
            "second",
            second,
            on_result=outcomes.append,
            on_error=outcomes.append,
            on_progress=outcomes.append,
        )
    finally:
        release.set()
    qtbot.waitUntil(lambda: not slot.pending)

    assert outcomes == ["current progress", "current result"]
    assert discarded == ["unwanted resource"]


def test_failure_finishes_pending_state_and_a_retry_succeeds() -> None:
    owner = QObject()
    slot = LatestRequest(owner)
    first, retry = _ServiceRequest(), _ServiceRequest()
    results: list[str] = []
    failures: list[str] = []

    def failed(error: Exception) -> None:
        assert not slot.pending
        failures.append(str(error))
        slot.start(retry.start, on_result=results.append, on_error=failed)

    slot.start(first.start, on_result=results.append, on_error=failed)
    first.fail("temporarily unavailable")
    assert slot.pending
    retry.succeed("recovered")

    assert failures == ["temporarily unavailable"]
    assert results == ["recovered"]
    assert not slot.pending
