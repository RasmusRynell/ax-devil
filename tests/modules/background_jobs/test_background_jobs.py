"""Background jobs run away from the GUI thread and hand back only what their owner still wants, on the GUI thread."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable
from functools import partial
from typing import TypeVar

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from pytestqt.qtbot import QtBot

from ax_devil.modules.background_jobs import BackgroundJobs, JobContext

_T = TypeVar("_T")


def _threads_started_by(action: Callable[[], _T]) -> tuple[_T, set[threading.Thread]]:
    baseline = set(threading.enumerate())
    result = action()
    return result, set(threading.enumerate()) - baseline


class _Owner:
    """What a controller is to its jobs: it receives their outcomes and may go away while they run."""

    def __init__(self) -> None:
        self.outcomes: list[object] = []

    def receive(self, outcome: object) -> None:
        self.outcomes.append(outcome)


def test_work_runs_off_the_gui_thread_and_its_progress_and_result_arrive_on_it(qtbot: QtBot) -> None:
    """Submitting returns at once; progress and then the result reach the owner on the GUI thread, and the worker
    stops once nothing is left to do."""
    jobs = BackgroundJobs()
    owner = QObject()
    release = threading.Event()
    work_thread: list[threading.Thread] = []
    delivered: list[tuple[object, threading.Thread]] = []

    def work(job: JobContext[str]) -> int:
        work_thread.append(threading.current_thread())
        job.report("halfway")
        assert release.wait(5)
        return 42

    def deliver(value: object) -> None:
        delivered.append((value, threading.current_thread()))

    _, workers = _threads_started_by(
        lambda: jobs.scope(owner).submit("answer", work, on_result=deliver, on_error=deliver, on_progress=deliver)
    )
    qtbot.waitUntil(lambda: len(delivered) == 1)
    release.set()
    qtbot.waitUntil(lambda: len(delivered) == 2)

    gui = threading.main_thread()
    assert work_thread[0] is not gui
    assert delivered == [("halfway", gui), (42, gui)]
    qtbot.waitUntil(lambda: not any(worker.is_alive() for worker in workers))


def test_a_failed_job_reports_its_error_and_later_jobs_still_run(qtbot: QtBot) -> None:
    """A job that raises reaches its owner as an error, and the worker goes on to run the next job."""
    jobs = BackgroundJobs(max_workers=1)
    owner = QObject()
    scope = jobs.scope(owner)
    errors: list[Exception] = []
    results: list[str] = []

    def broken(_job: JobContext[None]) -> str:
        raise OSError("Network is unreachable")

    scope.submit("broken", broken, on_result=results.append, on_error=errors.append)
    scope.submit("next", lambda _job: "next", on_result=results.append, on_error=errors.append)
    qtbot.waitUntil(lambda: results == ["next"])

    assert [str(error) for error in errors] == ["Network is unreachable"]


@pytest.mark.parametrize("end", ["close", "destroy owner"])
def test_an_ended_scope_skips_waiting_work_stops_running_work_and_delivers_nothing(qtbot: QtBot, end: str) -> None:
    """Once the owner is done, work not yet started never runs, running work is told to stop, and no progress or
    outcome reaches the owner; other owners' jobs carry on."""
    jobs = BackgroundJobs(max_workers=1)
    owner = QObject()
    scope = jobs.scope(owner)
    receiver = _Owner()
    started, release = threading.Event(), threading.Event()
    noticed: list[bool] = []
    ran: list[str] = []

    def running(job: JobContext[str]) -> str:
        started.set()
        assert release.wait(5)
        noticed.append(job.cancelled)
        job.report("too late")
        return "running"

    def waiting(_job: JobContext[None]) -> str:
        ran.append("waiting")
        return "waiting"

    scope.submit(
        "running", running, on_result=receiver.receive, on_error=receiver.receive, on_progress=receiver.receive
    )
    scope.submit("waiting", waiting, on_result=receiver.receive, on_error=receiver.receive)
    assert started.wait(5)
    if end == "close":
        scope.close()
    else:
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    scope.submit("after", waiting, on_result=receiver.receive, on_error=receiver.receive)
    release.set()
    other: list[object] = []
    other_owner = QObject()
    jobs.scope(other_owner).submit("other", lambda _job: "other", on_result=other.append, on_error=other.append)
    qtbot.waitUntil(lambda: other == ["other"])

    assert noticed == [True]
    assert ran == []
    assert receiver.outcomes == []


def test_cancelling_one_job_skips_it_and_leaves_the_scopes_other_jobs(qtbot: QtBot) -> None:
    """A job cancelled before its turn never runs or reports, while the rest of its scope still delivers."""
    jobs = BackgroundJobs(max_workers=1)
    owner = QObject()
    scope = jobs.scope(owner)
    release = threading.Event()
    ran: list[str] = []
    outcomes: list[object] = []

    def cancelled(_job: JobContext[None]) -> str:
        ran.append("cancelled")
        return "cancelled"

    scope.submit("busy", lambda _job: release.wait(5), on_result=outcomes.append, on_error=outcomes.append)
    cancel = scope.submit("cancelled", cancelled, on_result=outcomes.append, on_error=outcomes.append)
    scope.submit("kept", lambda _job: "kept", on_result=outcomes.append, on_error=outcomes.append)
    cancel()
    release.set()
    qtbot.waitUntil(lambda: outcomes == [True, "kept"])

    assert ran == []


def test_ending_a_scope_releases_its_owner_while_the_work_still_runs(qtbot: QtBot) -> None:
    """A slow job does not keep a closed owner alive through its callbacks."""
    jobs = BackgroundJobs()
    owner = QObject()
    scope = jobs.scope(owner)
    release = threading.Event()
    receiver = _Owner()
    gone = weakref.ref(receiver)
    scope.submit("slow", lambda _job: release.wait(5), on_result=receiver.receive, on_error=receiver.receive)
    del receiver
    assert gone() is not None, "the job holds its callbacks until the scope ends"

    scope.close()

    assert gone() is None
    release.set()


def test_the_work_and_its_result_are_released_on_the_gui_thread(qtbot: QtBot) -> None:
    """A worker never drops the last reference to what a job holds, so no Qt object is finalized on it."""
    jobs = BackgroundJobs()
    released_on: dict[str, threading.Thread] = {}

    class _Held:
        pass

    def track(name: str) -> _Held:
        held = _Held()
        weakref.finalize(held, lambda: released_on.setdefault(name, threading.current_thread()))
        return held

    work = partial(_track_and_return, track, track("work"))
    results: list[object] = []
    owner = QObject()
    jobs.scope(owner).submit("hold", work, on_result=results.append, on_error=results.append)
    del work
    qtbot.waitUntil(lambda: len(results) == 1)
    results.clear()

    assert released_on == {"work": threading.main_thread(), "result": threading.main_thread()}


def _track_and_return(track: Callable[[str], object], _captured: object, _job: JobContext[None]) -> object:
    return track("result")  # Made on the worker, so only the job holds it once the work returns.
