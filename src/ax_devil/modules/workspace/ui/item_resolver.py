"""Run Workspace work, chiefly resolving Items, away from the GUI thread."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

from PySide6.QtCore import QObject

from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, background_jobs
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import ItemResolution, ItemResolutionError, ResolutionContext, WorkspaceItem

logger = get_logger(__name__)

ResolutionsReady = Callable[[tuple[ItemResolution, ...]], None]


class ItemResolver(QObject):
    """Resolve batches of items through *jobs* and hand each result back on the GUI thread.

    Resolving reads files and runs playlist resolvers, which may take long on large or slow folders. Batches run on the
    application's background jobs unless *jobs* is given, so new work does not wait behind one slow batch, a cancelled
    batch that has not started is skipped, and quitting never waits for a resolver. Results may arrive in any order,
    and none arrive after the resolver is gone. With ``in_background=False`` every batch resolves at once on the
    calling thread instead, as tests need.
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
        self._jobs = (jobs or background_jobs()).scope(self) if in_background else None

    @property
    def context(self) -> ResolutionContext:
        """Return the services items resolve with."""
        return self._context

    def resolve(self, items: Sequence[WorkspaceItem], done: ResolutionsReady) -> Callable[[], None]:
        """Resolve *items* and call *done* with their results, in item order, on the GUI thread.

        Return a function that cancels the batch: *done* is not called, and items not yet resolving are not resolved.
        """
        items = tuple(items)
        return self.run(lambda: self._resolve_all(items), done)

    def run(self, work: Callable[[], Any], done: Callable[[Any], None]) -> Callable[[], None]:
        """Call *work* on a worker thread and hand its result to *done* on the GUI thread; return a canceller.

        *work* must not touch Qt objects or shared state. When it raises, the failure is logged and *done* is not
        called.
        """
        if self._jobs is None:
            done(work())
            return _nothing
        return self._jobs.submit("workspace", partial(_call, work), on_result=done, on_error=_logged)

    def _resolve_all(self, items: tuple[WorkspaceItem, ...]) -> tuple[ItemResolution, ...]:
        return tuple(self._resolve_one(item) for item in items)

    def _resolve_one(self, item: WorkspaceItem) -> ItemResolution:
        """Resolve *item*; an unexpected failure is recorded like any other, so no item waits forever."""
        try:
            return ItemResolution.of(item, self._context)
        except Exception as exc:
            logger.exception(f"Resolving {item.display_name} failed unexpectedly")
            return ItemResolution(item, error=ItemResolutionError(str(exc)))


def _call(work: Callable[[], Any], _job: JobContext[None]) -> Any:
    return work()


def _nothing() -> None:
    """Cancel work that already finished."""


def _logged(_error: Exception) -> None:
    """Drop a failure the background jobs already logged."""
