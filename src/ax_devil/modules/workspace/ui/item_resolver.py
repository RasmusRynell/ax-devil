"""Run Workspace work, chiefly resolving Items, away from the GUI thread."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from PySide6.QtCore import QObject

from ax_devil.core.cancellation import NEVER, CancelToken
from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, background_jobs
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import ItemResolution, ItemResolutionError, ResolutionContext, WorkspaceItem

logger = get_logger(__name__)

ResolutionReady = Callable[[ItemResolution], None]


class ItemResolver(QObject):
    """Resolve independent items on the workspace pool and deliver each outcome on the GUI thread.

    A slow item never holds up another pool, cancellation reaches the item's resolver, and destroying this owner
    ends delivery. ``in_background=False`` resolves at once on the calling thread, for tests.
    """

    def __init__(
        self,
        context: ResolutionContext,
        parent: QObject | None = None,
        *,
        in_background: bool = True,
        jobs: BackgroundJobs | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._jobs = (jobs or background_jobs("workspace")).scope(self) if in_background else None

    @property
    def context(self) -> ResolutionContext:
        """Return the services items resolve with."""
        return self._context

    def resolve(self, item: WorkspaceItem, done: ResolutionReady) -> Callable[[], None]:
        """Resolve *item*, call *done* on the GUI thread, and return its canceller."""
        context = self._context
        if self._jobs is None:
            done(_resolve_item(item, context, NEVER))
            return _nothing
        return self._jobs.submit(
            "workspace",
            lambda job: _resolve_item(item, context, job.token),
            on_result=done,
            on_error=lambda error: done(ItemResolution(item, error=ItemResolutionError(str(error)))),
        )

    def run(self, work: Callable[[], Any], done: Callable[[Any], None]) -> Callable[[], None]:
        """Call *work* on a worker thread and hand its result to *done* on the GUI thread; return a canceller.

        *work* must not touch Qt objects or shared state. When it raises, the failure is logged and *done* is not
        called.
        """
        if self._jobs is None:
            done(work())
            return _nothing
        return self._jobs.submit("workspace", partial(_call, work), on_result=done, on_error=_logged)


def _resolve_item(item: WorkspaceItem, context: ResolutionContext, token: CancelToken) -> ItemResolution:
    """Record unexpected failures like resolution errors, so no item waits forever."""
    try:
        return ItemResolution.of(item, context, token)
    except Exception as exc:
        logger.exception(f"Resolving {item.display_name} failed unexpectedly")
        return ItemResolution(item, error=ItemResolutionError(str(exc)))


def _call(work: Callable[[], Any], _job: JobContext[None]) -> Any:
    return work()


def _nothing() -> None:
    """Cancel work that already finished."""


def _logged(_error: Exception) -> None:
    """Drop a failure the background jobs already logged."""
