"""Run finite work away from the GUI thread and hand its progress and outcome back to the GUI thread."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from functools import partial
from typing import Any, Generic, TypeVar

from PySide6.QtCore import QObject, Qt, Signal, Slot

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_MAX_WORKERS = 4  # ponytail: shared by every job, so slow jobs delay others; give such work its own instance.

P = TypeVar("P")
R = TypeVar("R")


class _Job:
    """One submitted job. The GUI thread drops its callbacks; only its worker writes its outcome."""

    def __init__(
        self,
        name: str,
        work: Callable[[JobContext[Any]], Any],
        on_result: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[Any], None] | None,
        scope: JobScope,
    ) -> None:
        self.name = name
        self.work: Callable[[JobContext[Any]], Any] | None = work
        self.on_result: Callable[[Any], None] | None = on_result
        self.on_error: Callable[[Exception], None] | None = on_error
        self.on_progress = on_progress
        self.scope: JobScope | None = scope
        self.cancelled = threading.Event()
        self.result: Any = None
        self.error: Exception | None = None

    def cancel(self) -> None:
        """Stop wanting the outcome and drop the callbacks, which may hold the owner. GUI thread only.

        *work* stays until the job finishes, so a worker running it never holds its last reference.
        """
        self.cancelled.set()
        self.on_result = self.on_error = self.on_progress = None


class JobContext(Generic[P]):
    """What running work may use: report progress, and see whether its outcome is still wanted."""

    def __init__(self, job: _Job, jobs: BackgroundJobs) -> None:
        self._job = job
        self._jobs = jobs

    @property
    def cancelled(self) -> bool:
        """Return whether the outcome is no longer wanted; long work checks it between steps and stops early."""
        return self._job.cancelled.is_set()

    def report(self, progress: P) -> None:
        """Hand *progress*, a plain value, to the job's progress callback on the GUI thread."""
        # ponytail: one queued event per report; coalesce here if a job ever reports faster than the GUI can draw.
        if not self.cancelled:
            self._jobs.progressed.emit(self._job, progress)


class JobScope:
    """The jobs one owner wants the outcome of. Closing the scope, or destroying its owner, ends every delivery.

    Create one with `BackgroundJobs.scope`. Call everything on the GUI thread.
    """

    def __init__(self, jobs: BackgroundJobs) -> None:
        self._jobs = jobs
        self._pending: set[_Job] = set()
        self._closed = False

    def submit(
        self,
        name: str,
        work: Callable[[JobContext[P]], R],
        *,
        on_result: Callable[[R], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[P], None] | None = None,
    ) -> Callable[[], None]:
        """Run *work* on a worker thread and call exactly one of *on_result* or *on_error* on the GUI thread.

        *work* gets a `JobContext` to report progress to *on_progress* and to notice cancellation. It must use only
        the values it was given, never widgets or other Qt objects, must bound every blocking call with a timeout,
        and returns or reports plain values. An exception it raises is logged with its traceback under *name* and
        goes to *on_error*, so its message must not carry secrets. Return a function that cancels this job as
        closing the scope cancels all of them; once either happens nothing more is called, and work that has not
        started never runs.
        """
        job = _Job(name, work, on_result, on_error, on_progress, self)
        if self._closed:
            job.cancel()
            return job.cancel
        self._pending.add(job)
        self._jobs._start(job)
        return partial(self._cancel, job)

    def close(self) -> None:
        """Stop every delivery, skip work that has not started, and ask running work to stop. Never waits."""
        self._closed = True
        for job in self._pending:
            job.cancel()
        self._pending.clear()

    def _cancel(self, job: _Job) -> None:
        job.cancel()
        self._pending.discard(job)

    def _forget(self, job: _Job) -> None:
        self._pending.discard(job)


class BackgroundJobs(QObject):
    """Run finite jobs on a few daemon worker threads and deliver their outcomes on the GUI thread.

    For occasional work that would block the GUI, such as scanning the network; streams and long-lived workers own
    their threads. Owners submit through a `JobScope` that ends with them. Workers start when work is queued and stop
    when none is left, and are daemon threads, so quitting never waits for a job. Every object a job holds is
    released on the GUI thread. Jobs may finish in any order unless *max_workers* is 1. Use `background_jobs()` for
    the application's instance; create others only in tests, on the GUI thread.
    """

    progressed = Signal(object, object)  # _Job, the progress value
    finished = Signal(object)  # _Job

    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        super().__init__()
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._queue: deque[_Job] = deque()  # Guarded by _lock, with _workers.
        self._workers = 0
        self._unfinished: set[_Job] = set()  # Keeps each job alive until the GUI thread releases it.
        self.progressed.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.finished.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    def scope(self, owner: QObject) -> JobScope:
        """Return a new scope for jobs whose outcome *owner* wants; it closes when *owner* is destroyed."""
        scope = JobScope(self)
        owner.destroyed.connect(scope.close)
        return scope

    def _start(self, job: _Job) -> None:
        self._unfinished.add(job)
        with self._lock:
            self._queue.append(job)
            if self._workers == self._max_workers:
                return
            self._workers += 1
        threading.Thread(target=self._work, name="background-job", daemon=True).start()

    def _work(self) -> None:
        """Run queued jobs until none is left."""
        while True:
            with self._lock:
                if not self._queue:
                    self._workers -= 1
                    return
                job = self._queue.popleft()
            self._run(job)
            self.finished.emit(job)
            del job

    def _run(self, job: _Job) -> None:
        work = job.work
        if work is None or job.cancelled.is_set():
            return
        try:
            job.result = work(JobContext(job, self))
        except Exception as exc:
            # A warning: most failures are a device or file the user named, not a fault in the app.
            logger.warning(f"Background job '{job.name}' failed: {exc}", exc_info=True)
            job.error = exc.with_traceback(None)  # The traceback would keep the worker's frames alive.

    @Slot(object, object)
    def _progress(self, job: _Job, progress: object) -> None:
        if job.on_progress is not None:
            job.on_progress(progress)

    @Slot(object)
    def _finish(self, job: _Job) -> None:
        """Release everything *job* holds, then deliver its outcome unless it was cancelled."""
        self._unfinished.discard(job)
        if job.scope is not None:
            job.scope._forget(job)
        on_result, on_error = job.on_result, job.on_error
        result, error = job.result, job.error
        job.work = job.on_result = job.on_error = job.on_progress = job.scope = job.result = job.error = None
        if on_result is None or on_error is None:
            return
        if error is not None:
            on_error(error)
        else:
            on_result(result)


_shared: BackgroundJobs | None = None


def background_jobs() -> BackgroundJobs:
    """Return the application's `BackgroundJobs`, created on first use; the first call must be on the GUI thread."""
    global _shared
    if _shared is None:
        _shared = BackgroundJobs()
    return _shared
