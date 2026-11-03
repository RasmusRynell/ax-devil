"""Background jobs run away from the GUI thread and hand back only what their owner still wants, on the GUI thread."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable
from functools import partial
from typing import TypeVar

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import Cancelled
from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, background_jobs

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


@pytest.mark.parametrize("max_workers", [0, -1])
def test_nonpositive_worker_counts_are_rejected(max_workers: int) -> None:
    """Invalid pool sizes fail immediately instead of leaving jobs queued forever or uncapped."""
    with pytest.raises(ValueError):
        BackgroundJobs(max_workers=max_workers)


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
        try:
            job.report("too late")  # Reporting polls the token, so a cancelled job stops here.
        except Cancelled:
            noticed.append(True)
            raise
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


def test_a_result_nobody_wants_any_more_goes_to_discard_on_the_gui_thread(qtbot: QtBot) -> None:
    """A result that owns something is handed back for release when its job was cancelled while it ran, so it is
    not leaked; a delivered result, cancelled waiting work and an error never reach discard."""
    jobs = BackgroundJobs(max_workers=1)
    owner = QObject()
    scope = jobs.scope(owner)
    started, release = threading.Event(), threading.Event()
    outcomes: list[object] = []
    discarded: list[tuple[object, threading.Thread]] = []

    def discard(value: object) -> None:
        discarded.append((value, threading.current_thread()))

    def running(_job: JobContext[None]) -> str:
        started.set()
        assert release.wait(5)
        return "opened"

    def broken(_job: JobContext[None]) -> str:
        raise OSError("unreachable")

    def submit(name: str, work: Callable[[JobContext[None]], str]) -> Callable[[], None]:
        return scope.submit(name, work, on_result=outcomes.append, on_error=outcomes.append, on_discard=discard)

    cancel_running = submit("running", running)
    submit("delivered", lambda _job: "delivered")
    cancel_waiting = submit("waiting", lambda _job: "waiting")
    submit("broken", broken)
    assert started.wait(5)
    cancel_running()
    cancel_waiting()
    release.set()
    qtbot.waitUntil(lambda: len(outcomes) == 2)

    assert discarded == [("opened", threading.main_thread())]
    assert outcomes[0] == "delivered"
    assert isinstance(outcomes[1], OSError)


@pytest.mark.parametrize("end", ["close", "destroy owner"])
@pytest.mark.parametrize("when", ["running", "awaiting delivery"])
def test_ending_a_scope_discards_its_resource_result_once_on_the_gui_thread(qtbot: QtBot, end: str, when: str) -> None:
    """Closing an owner releases unwanted results even after work finished but before queued delivery."""
    jobs = BackgroundJobs(max_workers=1)
    owner = QObject()
    scope = jobs.scope(owner)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    outcomes: list[object] = []
    discarded: list[tuple[object, threading.Thread]] = []
    jobs.finished.connect(lambda _job: finished.set(), Qt.ConnectionType.DirectConnection)

    def work(_job: JobContext[None]) -> str:
        started.set()
        assert release.wait(5)
        return "opened"

    def discard(value: object) -> None:
        discarded.append((value, threading.current_thread()))

    scope.submit("open", work, on_result=outcomes.append, on_error=outcomes.append, on_discard=discard)
    try:
        assert started.wait(5)
        if when == "awaiting delivery":
            release.set()
            assert finished.wait(5)
        if end == "close":
            scope.close()
        else:
            owner.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(discarded))
    QCoreApplication.processEvents()

    assert outcomes == []
    assert discarded == [("opened", threading.main_thread())]


def test_a_pool_of_its_own_keeps_slow_work_away_from_the_shared_workers(qtbot: QtBot) -> None:
    """Work in a named pool has the size it asked for, and a busy pool does not delay the shared one."""
    name = f"test-{threading.get_ident()}-{id(qtbot)}"
    slow = background_jobs(name, max_workers=1)
    assert slow.max_workers == 1
    assert background_jobs(name) is slow
    with pytest.raises(ValueError):
        background_jobs(name, max_workers=2)
    assert background_jobs() is not slow
    release = threading.Event()
    owner = QObject()
    slow_outcomes: list[object] = []
    shared_outcomes: list[object] = []

    slow.scope(owner).submit(
        "slow", lambda _job: release.wait(5), on_result=slow_outcomes.append, on_error=slow_outcomes.append
    )
    background_jobs().scope(owner).submit(
        "quick", lambda _job: "quick", on_result=shared_outcomes.append, on_error=shared_outcomes.append
    )
    qtbot.waitUntil(lambda: shared_outcomes == ["quick"])

    assert slow_outcomes == []
    release.set()
    qtbot.waitUntil(lambda: slow_outcomes == [True])


def test_work_that_exits_the_worker_is_reported_as_an_error_and_the_pool_keeps_working(qtbot: QtBot) -> None:
    """A BaseException such as SystemExit from plugin code neither strands its job nor shrinks the pool."""
    jobs = BackgroundJobs(max_workers=1)
    owner = QObject()
    scope = jobs.scope(owner)
    outcomes: list[object] = []

    def leaving(_job: JobContext[None]) -> None:
        raise SystemExit(3)

    scope.submit("leaving", leaving, on_result=outcomes.append, on_error=outcomes.append)
    scope.submit("after", lambda _job: "after", on_result=outcomes.append, on_error=outcomes.append)
    qtbot.waitUntil(lambda: len(outcomes) == 2)

    assert isinstance(outcomes[0], RuntimeError) and "SystemExit" in str(outcomes[0])
    assert outcomes[1] == "after"


def test_work_interrupted_by_cancellation_delivers_nothing_and_the_pool_keeps_working(qtbot: QtBot) -> None:
    """Cancelled raised after the owner cancelled ends the job quietly: no result, no error, no discard; a plain
    `except Exception` in the work does not swallow it."""
    jobs = BackgroundJobs(max_workers=1)
    owner = QObject()
    scope = jobs.scope(owner)
    started, release = threading.Event(), threading.Event()
    outcomes: list[object] = []
    discarded: list[object] = []
    swallowed: list[Exception] = []

    def polling(job: JobContext[None]) -> str:
        started.set()
        assert release.wait(5)
        try:
            for _item in job.token.polled(range(3)):
                pass
        except Exception as exc:  # What plugin code often writes; it must not see the cancellation.
            swallowed.append(exc)
        return "finished anyway"

    cancel = scope.submit(
        "polling", polling, on_result=outcomes.append, on_error=outcomes.append, on_discard=discarded.append
    )
    scope.submit("after", lambda _job: "after", on_result=outcomes.append, on_error=outcomes.append)
    assert started.wait(5)
    cancel()
    release.set()
    qtbot.waitUntil(lambda: outcomes == ["after"])

    assert swallowed == []
    assert discarded == []


def test_cancelled_work_not_cancelled_by_its_owner_reports_an_error_instead_of_leaving_the_owner_waiting(
    qtbot: QtBot,
) -> None:
    """Cancelled from a token the job does not own is a bug in the work, and the owner hears of it."""
    jobs = BackgroundJobs(max_workers=1)
    owner = QObject()
    scope = jobs.scope(owner)
    outcomes: list[object] = []

    def stray(_job: JobContext[None]) -> None:
        raise Cancelled

    scope.submit("stray", stray, on_result=outcomes.append, on_error=outcomes.append)
    scope.submit("after", lambda _job: "after", on_result=outcomes.append, on_error=outcomes.append)
    qtbot.waitUntil(lambda: len(outcomes) == 2)

    assert isinstance(outcomes[0], RuntimeError) and "stray" in str(outcomes[0])
    assert outcomes[1] == "after"
