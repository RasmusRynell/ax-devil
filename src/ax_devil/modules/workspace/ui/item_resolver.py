"""Resolve Workspace Items away from the GUI thread."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import ItemResolution, ItemResolutionError, ResolutionContext, WorkspaceItem

logger = get_logger(__name__)

ResolutionsReady = Callable[[tuple[ItemResolution, ...]], None]


class _JobSignals(QObject):
    resolved = Signal(object, object)  # _ResolveJob, tuple[ItemResolution, ...]


class _ResolveJob(QRunnable):
    def __init__(self, resolve: Callable[[], tuple[ItemResolution, ...]], done: ResolutionsReady) -> None:
        super().__init__()
        self.signals = _JobSignals()
        self.done = done
        self._resolve = resolve

    def run(self) -> None:
        self.signals.resolved.emit(self, self._resolve())


class ItemResolver(QObject):
    """Resolve batches of items on one background thread, in the order asked, and hand each back on the GUI thread.

    Resolving reads files and runs playlist resolvers, which may take long on large or slow folders. With
    ``in_background=False`` every batch resolves at once on the calling thread instead, as tests need.
    """

    def __init__(
        self, context: ResolutionContext, parent: QObject | None = None, *, in_background: bool = True
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._pool: QThreadPool | None = None
        if in_background:
            self._pool = QThreadPool(self)
            self._pool.setMaxThreadCount(1)
        self._jobs: set[_ResolveJob] = set()

    @property
    def context(self) -> ResolutionContext:
        """Return the services items resolve with."""
        return self._context

    def resolve(self, items: Sequence[WorkspaceItem], done: ResolutionsReady) -> None:
        """Resolve *items* and call *done* with their results, in item order, on the GUI thread."""
        items = tuple(items)
        if self._pool is None:
            done(self._resolve_all(items))
            return
        job = _ResolveJob(lambda: self._resolve_all(items), done)
        job.setAutoDelete(False)
        job.signals.resolved.connect(self._deliver)
        self._jobs.add(job)
        self._pool.start(job)

    def _deliver(self, job: _ResolveJob, resolutions: tuple[ItemResolution, ...]) -> None:
        """Release the finished *job* and hand its *resolutions* to its callback, on the GUI thread."""
        self._jobs.discard(job)
        job.done(resolutions)

    def _resolve_all(self, items: tuple[WorkspaceItem, ...]) -> tuple[ItemResolution, ...]:
        return tuple(self._resolve_one(item) for item in items)

    def _resolve_one(self, item: WorkspaceItem) -> ItemResolution:
        """Resolve *item*; an unexpected failure is recorded like any other, so no item waits forever."""
        try:
            return ItemResolution.of(item, self._context)
        except Exception as exc:
            logger.exception(f"Resolving {item.display_name} failed unexpectedly")
            return ItemResolution(item, error=ItemResolutionError(str(exc)))
