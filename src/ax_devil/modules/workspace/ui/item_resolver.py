"""Resolve Workspace Items away from the GUI thread."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import ItemResolution, ItemResolutionError, ResolutionContext, WorkspaceItem

logger = get_logger(__name__)

ResolutionsReady = Callable[[tuple[ItemResolution, ...]], None]


class _Batch(QObject):
    """One batch on its way back to the GUI thread; created there, so its signal is queued to the resolver."""

    resolved = Signal(object, object)  # _Batch, tuple[ItemResolution, ...]

    def __init__(self, done: ResolutionsReady) -> None:
        super().__init__()
        self.done = done


class ItemResolver(QObject):
    """Resolve batches of items on background threads and hand each result back on the GUI thread.

    Resolving reads files and runs playlist resolvers, which may take long on large or slow folders. Every batch gets
    its own daemon thread, so new work never waits behind a slow batch whose result will be dropped, and quitting
    never waits for a resolver; results may arrive in any order, and none arrive after the resolver is gone. With
    ``in_background=False`` every batch resolves at once on the calling thread instead, as tests need.
    """

    def __init__(
        self, context: ResolutionContext, parent: QObject | None = None, *, in_background: bool = True
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._in_background = in_background
        self._batches: set[_Batch] = set()  # Kept until delivered, so each is released on the GUI thread.

    @property
    def context(self) -> ResolutionContext:
        """Return the services items resolve with."""
        return self._context

    def resolve(self, items: Sequence[WorkspaceItem], done: ResolutionsReady) -> None:
        """Resolve *items* and call *done* with their results, in item order, on the GUI thread."""
        items = tuple(items)
        if not self._in_background:
            done(self._resolve_all(items))
            return
        batch = _Batch(done)
        batch.resolved.connect(self._deliver)
        self._batches.add(batch)
        threading.Thread(
            target=lambda: batch.resolved.emit(batch, self._resolve_all(items)), name="item-resolver", daemon=True
        ).start()

    def _deliver(self, batch: _Batch, resolutions: tuple[ItemResolution, ...]) -> None:
        """Release the finished *batch* and hand its *resolutions* to its callback, on the GUI thread."""
        self._batches.discard(batch)
        batch.done(resolutions)

    def _resolve_all(self, items: tuple[WorkspaceItem, ...]) -> tuple[ItemResolution, ...]:
        return tuple(self._resolve_one(item) for item in items)

    def _resolve_one(self, item: WorkspaceItem) -> ItemResolution:
        """Resolve *item*; an unexpected failure is recorded like any other, so no item waits forever."""
        try:
            return ItemResolution.of(item, self._context)
        except Exception as exc:
            logger.exception(f"Resolving {item.display_name} failed unexpectedly")
            return ItemResolution(item, error=ItemResolutionError(str(exc)))
