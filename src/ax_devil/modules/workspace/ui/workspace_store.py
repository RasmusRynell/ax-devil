"""The current Workspace, what its items resolved to, exclusions, and bookmarks, with Qt change signals."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.background_jobs.request import Cancel
from ax_devil.modules.file_watching import FileWatcher
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkKind,
    BookmarkTarget,
    ConsiderationItemRef,
    Content,
    ItemResolution,
    Workspace,
    WorkspaceItem,
    load_workspace,
    save_workspace,
)
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver

logger = get_logger(__name__)


class WorkspaceStore(QObject):
    """Own the current and last saved Workspace, each item's resolution, and exclusions.

    Items are the truth. Every change to them applies at once; their Content follows when *resolver* finishes, and
    until then an item's resolution is pending. A result arriving after its item was removed, or after another
    Workspace replaced this one, is dropped; one arriving after a rename is named after the new label. Exclusions are
    session state and are never part of the Workspace. Every successful mutation emits exactly one signal about the
    items, then ``items_resolved`` when their results arrive, plus ``state_changed`` when it changes whether the
    Workspace is modified or what it is called. A change to bookmarks or reviewed targets emits ``bookmarks_changed``
    once, also when removing an item removed some of them and whenever another Workspace replaces this one.

    What an item was read from (``ItemResolution.sources``) is watched once its first result arrives. When it changes,
    ``sources_changed`` reports the changed paths and the item resolves again; the new result, an error included,
    replaces the old one while the item and Workspace are still current and arrives as ``items_resolved`` without
    opening anything. An error keeps the files the last success was read from watched. Exclusions stay for Content
    that still exists and are dropped for Content that is gone. Resolving again never opens a viewer, never changes
    what open viewers show, since they keep the immutable Content they opened, and never makes the Workspace modified,
    as results are never saved.
    """

    items_added = Signal(list)  # list[WorkspaceItem]; they may still be resolving
    items_resolved = Signal(list, bool)  # list[ItemResolution], and whether to open the first and report failures
    item_removed = Signal(object)  # WorkspaceItem
    item_renamed = Signal(object)  # WorkspaceItem, with its new label
    item_consideration_changed = Signal(object, bool)
    sources_changed = Signal(object)  # frozenset[Path] of the watched paths that changed; their items resolve again
    workspace_replaced = Signal()  # another Workspace was opened or created; every item may be new
    bookmarks_changed = Signal()  # bookmarks or reviewed targets changed
    state_changed = Signal()  # is_modified, the name, or the path changed

    def __init__(
        self, resolver: ItemResolver, parent: QObject | None = None, *, watcher: FileWatcher | None = None
    ) -> None:
        super().__init__(parent)
        self._resolver = resolver
        self._watcher = FileWatcher(self) if watcher is None else watcher
        self._watcher.changed.connect(self._on_sources_changed)
        self._workspace = Workspace()
        self._saved_workspace: Workspace | None = self._workspace
        self._resolutions: dict[str, ItemResolution] = {}
        self._not_considered: set[ConsiderationItemRef] = set()
        self._generation = 0  # counts replaced Workspaces, so results for an earlier one are dropped
        self._reresolving: dict[str, Cancel] = {}  # cancel each re-resolving item's newest request
        # Ordered GUI-side batches; each item has its own cancellable worker.
        self._resolving: dict[int, _ResolutionBatch] = {}
        self._batch_count = 0
        self._announced_state = self._state()

    @property
    def workspace(self) -> Workspace:
        """Return the current Workspace."""
        return self._workspace

    @property
    def bookmarks(self) -> tuple[Bookmark, ...]:
        """Return the current Workspace's bookmarks, in the order they were made."""
        return self._workspace.bookmarks

    @property
    def reviewed(self) -> frozenset[BookmarkTarget]:
        """Return the targets of the current Workspace marked as reviewed."""
        return self._workspace.reviewed

    @property
    def is_modified(self) -> bool:
        """Return whether the current Workspace differs from the last saved one."""
        return self._workspace != self._saved_workspace

    def open_workspace(self, path: Path) -> None:
        """Replace the Workspace with the one saved in *path* and resolve its items.

        A ``WorkspaceFileError`` from reading the file propagates and leaves the store as it was.
        """
        self.replace_workspace(load_workspace(path))

    def replace_workspace(self, current: Workspace, *, unsaved: bool = False) -> None:
        """Make *current* the Workspace and, unless *unsaved*, its saved state, and resolve every item again in the
        background.

        An *unsaved* Workspace, such as a restored recovery copy, counts as modified until it is saved. Exclusions
        start from their defaults.
        """
        self._workspace = current
        self._saved_workspace = None if unsaved else current
        self._resolutions = {item.id: ItemResolution.pending(item) for item in current.items}
        self._not_considered = set()
        self._generation += 1
        cancels = [*(batch.cancel for batch in self._resolving.values()), *self._reresolving.values()]
        self._resolving, self._reresolving = {}, {}
        self._watcher.clear()
        for cancel in cancels:
            cancel()
        self.workspace_replaced.emit()
        self.bookmarks_changed.emit()
        self._announce_state()
        self._resolve(current.items, open_first=False)

    def save_workspace(self, path: Path | None = None) -> None:
        """Save the Workspace to *path*, or to its own file when None, and mark it as saved.

        Raises ``ValueError`` for a Workspace without a file when there is no *path*, and ``WorkspaceFileError`` when
        writing fails.
        """
        target = path or self._workspace.path
        if target is None:
            raise ValueError("A workspace without a file needs a path to be saved.")
        self._workspace = self._saved_workspace = save_workspace(self._workspace, target)
        self._announce_state()

    def rename_workspace(self, name: str) -> None:
        """Call the Workspace *name*, which is saved with it; an empty *name* is "Untitled"."""
        self._workspace = self._workspace.renamed(name)
        self._announce_state()

    def add_items(self, items: Sequence[WorkspaceItem], *, open_first: bool = True) -> None:
        """Append *items* and resolve them in the background; an item that fails to resolve stays, with its error.

        Once they resolve, the first opens and those that cannot open are reported, unless *open_first* is False.
        """
        self._append(items)
        self._resolve(items, open_first=open_first)

    def add_resolved(self, resolutions: Sequence[ItemResolution], *, open_first: bool = True) -> None:
        """Append items a dialog already resolved, with their results, so they are not resolved again; as with
        ``add_items``, the first opens unless *open_first* is False."""
        self._append([resolution.item for resolution in resolutions])
        self._commit(self._generation, tuple(resolutions), open_first=open_first)

    def remove_item(self, item_id: str) -> None:
        """Remove the item with *item_id*, its Content, its exclusions, and its bookmarks and reviewed targets."""
        if not self._workspace.has_item(item_id):
            logger.warning(f"Attempted to remove an item not in the workspace: {item_id}")
            return
        item = self._workspace.item(item_id)
        before = self._workspace
        self._workspace = self._workspace.remove_item(item_id)
        del self._resolutions[item_id]
        self._watcher.unwatch(item_id)
        self._cancel_reresolving(item_id)
        self._cancel_resolving(item_id)
        self._not_considered = {ref for ref in self._not_considered if ref.item_id != item_id}
        logger.debug(f"Item removed: {item.display_name} ({item_id})")
        self.item_removed.emit(item)
        if _bookmark_state(before) != _bookmark_state(self._workspace):
            self.bookmarks_changed.emit()
        self._announce_state()

    def rename_item(self, item_id: str, label: str) -> None:
        """Relabel the item with *item_id* and name its kept Content again; nothing is resolved.

        Content ids, errors, and exclusions stay as they were. An empty *label* returns the item to its default name.
        """
        if not self._workspace.has_item(item_id):
            logger.warning(f"Attempted to rename an item not in the workspace: {item_id}")
            return
        self._workspace = self._workspace.rename_item(item_id, label)
        item = self._workspace.item(item_id)
        self._resolutions[item_id] = self._resolutions[item_id].renamed(item)
        self.item_renamed.emit(item)
        self._announce_state()

    def add_bookmark(
        self, target: BookmarkTarget, frame: int, time_s: float, kind: BookmarkKind = BookmarkKind.NOTE, note: str = ""
    ) -> Bookmark | None:
        """Bookmark *frame* of *target* and return the new bookmark.

        Returns None and changes nothing when the target's item is missing or not bookmarkable, or the frame or time
        is invalid.
        """
        try:
            bookmark = Bookmark(target=target, frame=frame, time_s=time_s, kind=kind, note=note)
            workspace = self._workspace.add_bookmark(bookmark)
        except ValueError as exc:
            logger.warning(f"Ignoring a bookmark that cannot be added: {exc}")
            return None
        self._apply_bookmarks(workspace)
        return bookmark

    def remove_bookmark(self, bookmark_id: str) -> None:
        """Remove the bookmark with *bookmark_id*."""
        try:
            workspace = self._workspace.remove_bookmark(bookmark_id)
        except KeyError:
            logger.warning(f"Attempted to remove a bookmark not in the workspace: {bookmark_id}")
            return
        self._apply_bookmarks(workspace)

    def edit_bookmark(self, bookmark_id: str, *, note: str | None = None, kind: BookmarkKind | None = None) -> None:
        """Replace the note and kind of the bookmark with *bookmark_id* where given."""
        try:
            workspace = self._workspace.edit_bookmark(bookmark_id, note=note, kind=kind)
        except KeyError:
            logger.warning(f"Attempted to edit a bookmark not in the workspace: {bookmark_id}")
            return
        self._apply_bookmarks(workspace)

    def set_reviewed(self, target: BookmarkTarget, reviewed: bool) -> None:
        """Mark *target* as reviewed or not; a missing item, or marking on a non-bookmarkable one, is ignored."""
        try:
            workspace = self._workspace.set_reviewed(target, reviewed)
        except ValueError as exc:
            logger.warning(f"Ignoring a review change that cannot be made: {exc}")
            return
        self._apply_bookmarks(workspace)

    def _on_sources_changed(self, item_id: str, paths: frozenset[Path]) -> None:
        """Report the changed *paths* and resolve the item that was read from them again.

        The latest request is the one that counts: the earlier one is cancelled, so its result never arrives, and one
        still waiting its turn never runs.
        """
        if not self._workspace.has_item(item_id):
            return
        logger.debug(f"Sources of {self._workspace.item(item_id).display_name} changed: {sorted(paths)}")
        self.sources_changed.emit(paths)
        self._cancel_reresolving(item_id)
        self._reresolving[item_id] = self._resolver.resolve(
            self._workspace.item(item_id), partial(self._apply_reresolved, self._generation)
        )

    def _cancel_reresolving(self, item_id: str) -> None:
        """Cancel the new resolution of the item with *item_id* that is still under way, if any."""
        cancel = self._reresolving.pop(item_id, None)
        if cancel is not None:
            cancel()

    def _cancel_resolving(self, item_id: str) -> None:
        """Cancel only this item's work and let its batch finish with any remaining items."""
        for batch in tuple(self._resolving.values()):
            batch.remove(item_id)

    def contents(self) -> tuple[Content, ...]:
        """Return the Content of every resolved item, in item order."""
        return tuple(content for item in self._workspace.items for content in self._resolutions[item.id].contents)

    def resolution(self, item_id: str) -> ItemResolution:
        """Return what the item with *item_id* resolved to."""
        return self._resolutions[item_id]

    def resolutions(self) -> tuple[ItemResolution, ...]:
        """Return what every item resolved to, in item order."""
        return tuple(self._resolutions[item.id] for item in self._workspace.items)

    def is_item_considered(self, item_ref: ConsiderationItemRef) -> bool:
        """Return whether the referenced item participates in navigation/layout."""
        return item_ref not in self._not_considered

    def set_item_considered(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Update consideration state and emit a change signal when it changes."""
        if not any(item.ref == item_ref for content in self.contents() for item in content.consideration_items()):
            logger.warning(f"Ignoring consideration change for unknown Workspace item: {item_ref}")
            return
        if self.is_item_considered(item_ref) == considered:
            return
        if considered:
            self._not_considered.discard(item_ref)
        else:
            self._not_considered.add(item_ref)
        logger.debug(f"Item consideration changed: {item_ref} considered={considered}")
        self.item_consideration_changed.emit(item_ref, considered)

    def _state(self) -> tuple[bool, str, Path | None]:
        """Return what a title bar shows: whether the Workspace is modified, its name, and its file."""
        return self.is_modified, self._workspace.name, self._workspace.path

    def _announce_state(self) -> None:
        """Emit ``state_changed`` when the modified flag, name, or path differ from the last announcement."""
        state = self._state()
        if state != self._announced_state:
            self._announced_state = state
            self.state_changed.emit()

    def _apply_bookmarks(self, workspace: Workspace) -> None:
        """Make *workspace*, which differs from the current one at most in bookmarks, current when it changed them."""
        if _bookmark_state(workspace) == _bookmark_state(self._workspace):
            return
        self._workspace = workspace
        self.bookmarks_changed.emit()
        self._announce_state()

    def _append(self, items: Sequence[WorkspaceItem]) -> None:
        """Append *items*, each pending until its result is committed."""
        if not items:
            return
        self._workspace = self._workspace.add_items(items)
        self._resolutions.update((item.id, ItemResolution.pending(item)) for item in items)
        self.items_added.emit(list(items))
        self._announce_state()

    def _resolve(self, items: Sequence[WorkspaceItem], *, open_first: bool) -> None:
        """Resolve *items* in the background and commit the results to the Workspace they belong to.

        With *open_first*, items are published together in their original order, so the first of them is the one that
        opens. Otherwise each result is published independently, so a slow item never holds back the others.
        """
        generation = self._generation
        batches = [items] if open_first else [[item] for item in items]
        for batch in batches:
            if batch:
                self._batch_count += 1
                number = self._batch_count
                done = partial(self._batch_resolved, number, generation, open_first=open_first)
                pending = _ResolutionBatch(batch, done)
                self._resolving[number] = pending
                pending.start(self._resolver)

    def _batch_resolved(
        self, number: int, generation: int, resolutions: tuple[ItemResolution, ...], *, open_first: bool
    ) -> None:
        """Forget the finished batch *number*, then commit its results."""
        self._resolving.pop(number, None)
        self._commit(generation, resolutions, open_first=open_first)

    def _commit(self, generation: int, resolutions: tuple[ItemResolution, ...], *, open_first: bool) -> None:
        """Record the results of items still waiting for them, and exclude Content whose default is not considered."""
        if generation != self._generation:
            return
        committed = []
        for resolution in resolutions:
            waiting = self._resolutions.get(resolution.item.id)
            if waiting is None or not waiting.is_pending:
                continue
            committed.append(self._take(resolution.renamed(waiting.item), waiting))
        if committed:
            self.items_resolved.emit(committed, open_first)

    def _apply_reresolved(self, generation: int, resolution: ItemResolution) -> None:
        """Replace an item's result with its new one, unless its Workspace was replaced; a removed item's or a
        superseded resolution was cancelled and never arrives."""
        if generation != self._generation:
            return
        item_id = resolution.item.id
        self._reresolving.pop(item_id, None)
        current = self._take(resolution.renamed(self._workspace.item(item_id)), self._resolutions[item_id])
        self.items_resolved.emit([current], False)

    def _take(self, current: ItemResolution, previous: ItemResolution) -> ItemResolution:
        """Store *current* as the item's result in place of *previous*, watch what it was read from, and return it.

        Exclusions name entries, so they stay with the same ones: an entry that is gone loses its exclusion and one that
        is new starts at its default. A failed result keeps watching what the last success was read from, since
        restoring those files is what makes the item resolve again.
        """
        if current.error is not None:
            logger.warning(f"Could not open {current.item.display_name}: {current.error}")
            current = replace(current, sources=current.sources | previous.sources)
        self._resolutions[current.item.id] = current
        before, after = _defaults(previous), _defaults(current)
        self._not_considered = {ref for ref in self._not_considered if ref not in before or ref in after} | {
            ref for ref, considered in after.items() if ref not in before and not considered
        }
        self._watcher.watch(current.item.id, current.sources)
        return current


class _ResolutionBatch:
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
        if cancel is not None:
            cancel()
        del self._results[item_id]
        self._publish_if_ready()

    def cancel(self) -> None:
        """Cancel the entire batch without publishing it."""
        for cancel in self._cancels.values():
            cancel()
        self._cancels.clear()

    def _resolved(self, result: ItemResolution) -> None:
        self._cancels.pop(result.item.id, None)
        self._results[result.item.id] = result
        self._publish_if_ready()

    def _publish_if_ready(self) -> None:
        completed = tuple(result for result in self._results.values() if result is not None)
        if len(completed) == len(self._results):
            self._done(completed)


def _bookmark_state(workspace: Workspace) -> tuple[tuple[Bookmark, ...], frozenset[BookmarkTarget]]:
    """Return what ``bookmarks_changed`` reports on: the Workspace's bookmarks and reviewed targets."""
    return workspace.bookmarks, workspace.reviewed


def _defaults(resolution: ItemResolution) -> dict[ConsiderationItemRef, bool]:
    """Return whether each consideration item of *resolution*'s Content is considered by default, by its ref."""
    return {
        consideration.ref: consideration.default_considered
        for content in resolution.contents
        for consideration in content.consideration_items()
    }
