"""Run finite work away from the GUI thread and hand its progress and outcome back to the GUI thread."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from functools import partial
from typing import Any, Generic, TypeVar

from PySide6.QtCore import QObject, Qt, Signal, Slot

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_MAX_WORKERS = 4  # ponytail: shared by every job in a pool, so slow jobs delay others; give such work its own pool.

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
        on_discard: Callable[[Any], None] | None,
        scope: JobScope,
    ) -> None:
        self.name = name
        self.work: Callable[[JobContext[Any]], Any] | None = work
        self.on_result: Callable[[Any], None] | None = on_result
        self.on_error: Callable[[Exception], None] | None = on_error
        self.on_progress = on_progress
        self.on_discard = on_discard
        self.scope: JobScope | None = scope
        self.token = CancelToken()
        self.result: Any = None
        self.has_result = False  # The result may be None.
        self.interrupted = False  # Work raised Cancelled.
        self.error: Exception | None = None

    def cancel(self) -> None:
        """Stop wanting the outcome and drop the callbacks, which may hold the owner. GUI thread only.

        *work* stays until the job finishes, so a worker running it never holds its last reference. *on_discard* stays
        too, since it is for a result that arrives after this.
        """
        self.token.cancel()
        self.on_result = self.on_error = self.on_progress = None


class JobContext(Generic[P]):
    """What running work may use: report progress, and learn through its token that its outcome is no longer wanted."""

    def __init__(self, job: _Job, jobs: BackgroundJobs) -> None:
        self._job = job
        self._jobs = jobs

    @property
    def token(self) -> CancelToken:
        """Return the job's token; work polls it between operations and never cancels it."""
        return self._job.token

    def report(self, progress: P) -> None:
        """Hand *progress*, a plain value, to the job's progress callback on the GUI thread.

        Raises `Cancelled` once the job is cancelled, like any other poll of the token.
        """
        # ponytail: one queued event per report; coalesce here if a job ever reports faster than the GUI can draw.
        self.token.raise_if_cancelled()
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
        on_discard: Callable[[R], None] | None = None,
    ) -> Callable[[], None]:
        """Run *work* on a worker thread and call exactly one of *on_result* or *on_error* on the GUI thread.

        *work* gets a `JobContext` to report progress to *on_progress* and, through ``job.token``, to learn it was
        cancelled: it polls the token between operations and `Cancelled` ends it. It must use only the values it was
        given, never widgets or other Qt objects, use timeouts where blocking APIs support them, and return or report
        plain values. Cancellation cannot interrupt a blocking call; potentially slow filesystem work uses its own
        pool, since file APIs may have no timeout. An exception it raises is logged with its traceback under *name* and
        goes to *on_error*, so its message must not carry secrets. Return a function that cancels this job as closing
        the scope cancels all of them; once either happens nothing more is delivered, and work that has not started
        never runs.

        A result that owns something, such as a running source, would leak when its job is cancelled before delivery.
        Pass *on_discard* to receive such a result on the GUI thread and release it. It is called only for
        a result nobody asked for any more, never for a delivered result, skipped or cancelled work or an error, and
        must not hold the owner. Work that `Cancelled` interrupts has no result, so it releases what it holds itself.
        """
        job = _Job(name, work, on_result, on_error, on_progress, on_discard, self)
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
    released on the GUI thread. Jobs may finish in any order unless *max_workers* is 1. Get the application's
    instances from `background_jobs()`; create one directly only in tests, on the GUI thread.
    """

    progressed = Signal(object, object)  # _Job, the progress value
    finished = Signal(object)  # _Job

    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        if max_workers <= 0:
            raise ValueError("Background jobs need at least one worker.")
        super().__init__()
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._queue: deque[_Job] = deque()  # Guarded by _lock, with _workers.
        self._workers = 0
        self._unfinished: set[_Job] = set()  # Keeps each job alive until the GUI thread releases it.
        self.progressed.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.finished.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    @property
    def max_workers(self) -> int:
        """Return how many jobs run at once."""
        return self._max_workers

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
        assert work is not None  # Cleared only once the job has finished.
        try:
            job.token.raise_if_cancelled()  # Cancelled while queued: never started.
            job.result = work(JobContext(job, self))
            job.has_result = True
        except Cancelled:
            job.interrupted = True
        except Exception as exc:
            # A warning: most failures are a device or file the user named, not a fault in the app.
            logger.warning(f"Background job '{job.name}' failed: {exc}", exc_info=True)
            job.error = exc.with_traceback(None)  # The traceback would keep the worker's frames alive.
        except BaseException as exc:  # e.g. sys.exit() in plugin code; the worker must survive to deliver
            logger.warning(f"Background job '{job.name}' ended with {type(exc).__name__}: {exc}", exc_info=True)
            job.error = RuntimeError(f"{type(exc).__name__}: {exc}")

    @Slot(object, object)
    def _progress(self, job: _Job, progress: object) -> None:
        if job.on_progress is not None:
            job.on_progress(progress)

    @Slot(object)
    def _finish(self, job: _Job) -> None:
        """Release everything *job* holds, then deliver its outcome, or hand a result nobody wants to *on_discard*."""
        self._unfinished.discard(job)
        if job.scope is not None:
            job.scope._forget(job)
        on_result, on_error, on_discard = job.on_result, job.on_error, job.on_discard
        result, error, has_result, interrupted = job.result, job.error, job.has_result, job.interrupted
        job.work = job.on_result = job.on_error = job.on_progress = job.on_discard = job.scope = None
        job.result = job.error = None
        if on_result is None or on_error is None:
            if on_discard is not None and has_result:
                on_discard(result)
            return
        if interrupted:
            # Cancelling the job drops its callbacks, so Cancelled reached here from another token: a bug in the work.
            # The owner hears of it instead of waiting for an outcome that never comes.
            logger.warning(f"Background job '{job.name}' raised Cancelled, but it was not cancelled")
            on_error(RuntimeError(f"Background job '{job.name}' stopped as cancelled, though it was not"))
        elif error is not None:
            on_error(error)
        else:
            on_result(result)


_pools: dict[str, BackgroundJobs] = {}


def background_jobs(pool: str = "shared", *, max_workers: int | None = None) -> BackgroundJobs:
    """Return the application's workers named *pool*, created on first use; call it on the GUI thread.

    Work that is slow, or must run one job at a time, asks for a pool of its own so it neither delays other work nor
    is delayed by it. The first call for a *pool* sets its *max_workers* (default 4); a later call that names another
    size raises ``ValueError``.
    """
    jobs = _pools.get(pool)
    if jobs is None:
        jobs = _pools[pool] = BackgroundJobs(_MAX_WORKERS if max_workers is None else max_workers)
    elif max_workers not in (None, jobs.max_workers):
        raise ValueError(f"Background pool '{pool}' has {jobs.max_workers} workers, not {max_workers}.")
    return jobs
