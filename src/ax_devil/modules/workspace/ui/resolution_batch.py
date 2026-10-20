"""A group of item resolutions published together."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ax_devil.modules.background_jobs.request import Cancel
from ax_devil.modules.workspace.core import ItemResolution, WorkspaceItem
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver


class ResolutionBatch:
    """Publish independent item jobs together in input order; all state belongs to the GUI thread."""

    def __init__(self, items: Sequence[WorkspaceItem], done: Callable[[tuple[ItemResolution, ...]], None]) -> None:
        self._items = tuple(items)
        self._results: dict[str, ItemResolution | None] = dict.fromkeys(item.id for item in items)
        self._cancels: dict[str, Cancel] = {}
        self._done = done

    def start(self, resolver: ItemResolver) -> None:
        """Start one cancellable job per item, including resolvers that finish inline."""
        for item in self._items:
            cancel = resolver.resolve(item, self._resolved)
            if item.id in self._results and self._results[item.id] is None:
                self._cancels[item.id] = cancel

    def remove(self, item_id: str) -> None:
        """Drop a removed item, interrupting its worker even if the rest of the batch is still wanted."""
        if item_id not in self._results:
            return
        cancel = self._cancels.pop(item_id, None)
        del self._results[item_id]
        if cancel is not None:
            cancel()
        self._publish_if_ready()

    def cancel(self) -> None:
        """Cancel the entire batch without publishing it."""
        cancels = tuple(self._cancels.values())
        self._cancels.clear()
        self._results.clear()
        for cancel in cancels:
            cancel()

    def _resolved(self, result: ItemResolution) -> None:
        if result.item.id not in self._results or self._results[result.item.id] is not None:
            return
        self._cancels.pop(result.item.id, None)
        self._results[result.item.id] = result
        self._publish_if_ready()

    def _publish_if_ready(self) -> None:
        completed = tuple(result for result in self._results.values() if result is not None)
        if len(completed) == len(self._results):
            self._done(completed)
