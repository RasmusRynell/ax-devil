"""The current Workspace, what its items resolved to, exclusions, and bookmarks, with Qt change signals."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Signal

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
    once, also when removing an item removed some of them and whenever another Workspace replaces this one. A rescan
    emits ``item_rescanned`` when its result is applied.
    """

    items_added = Signal(list)  # list[WorkspaceItem]; they may still be resolving
    items_resolved = Signal(list, bool)  # list[ItemResolution], and whether to open the first and report failures
    item_removed = Signal(object)  # WorkspaceItem
    item_renamed = Signal(object)  # WorkspaceItem, with its new label
    item_consideration_changed = Signal(object, bool)
    item_rescanned = Signal(object)  # ItemResolution, the item's new result
    workspace_replaced = Signal()  # another Workspace was opened, created, or restored; every item may be new
    bookmarks_changed = Signal()  # bookmarks or reviewed targets changed
    state_changed = Signal()  # is_modified, the name, or the path changed

    def __init__(self, resolver: ItemResolver, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._resolver = resolver
        self._workspace = Workspace()
        self._saved_workspace = self._workspace
        self._awaiting_saved = False  # a restored Workspace's saved state is still being read
        self._read_saved: Callable[[], tuple[Workspace, Workspace]] | None = None  # reads it, for finish_restore
        self._cancel_read: Callable[[], None] | None = None  # cancels the background read of the saved state
        self._resolutions: dict[str, ItemResolution] = {}
        self._not_considered: set[ConsiderationItemRef] = set()
        self._generation = 0  # counts replaced Workspaces, so results for an earlier one are dropped
        self._rescans: dict[str, Callable[[], None]] = {}  # cancel each rescanning item's newest request
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

    def replace_workspace(self, current: Workspace, saved: Workspace | None = None) -> None:
        """Make *current* the Workspace, with *saved* as its last saved state (*current* itself when None).

        Every item is resolved again, in the background, and exclusions start from their defaults. Restoring a kept
        Workspace passes the file's content as *saved*, so its unsaved edits still count as modified.
        """
        self._workspace = current
        self._saved_workspace = current if saved is None else saved
        self._resolutions = {item.id: ItemResolution.pending(item) for item in current.items}
        self._not_considered = set()
        self._generation += 1
        cancels = [*(batch.cancel for batch in self._resolving.values()), *self._rescans.values()]
        self._resolving, self._rescans = {}, {}
        for cancel in cancels:
            cancel()
        self._settle_saved()
        self.workspace_replaced.emit()
        self.bookmarks_changed.emit()
        self._announce_state()
        self._resolve(current.items, open_first=False)

    def restore_workspace(
        self, kept: Workspace, load_saved: Callable[[Workspace], tuple[Workspace, Workspace]]
    ) -> None:
        """Make *kept* the Workspace now and publish its saved state when *load_saved* has read it in the background.

        Until then *kept* counts as saved. *load_saved* returns ``(current, saved)`` and may take long; when it reports
        a different path, the Workspace takes that path. Its result is dropped when another Workspace replaced this one
        or the user saved meanwhile, or ``finish_restore`` published it first.
        """
        self.replace_workspace(kept)
        self._awaiting_saved = True
        self._read_saved = partial(load_saved, kept)
        generation = self._generation
        self._cancel_read = self._resolver.run(self._read_saved, partial(self._publish_saved, generation))

    def finish_restore(self) -> None:
        """Read the saved state of a restored Workspace now, on the calling thread, when it is still pending.

        Call it before a decision that needs the real saved state, such as asking about unsaved edits; it blocks as
        long as the file read takes. The background result that arrives later is dropped.
        """
        if self._awaiting_saved and self._read_saved is not None:
            self._publish_saved(self._generation, self._read_saved())

    def save_workspace(self, path: Path | None = None) -> None:
        """Save the Workspace to *path*, or to its own file when None, and mark it as saved.

        Raises ``ValueError`` for a Workspace without a file when there is no *path*, and ``WorkspaceFileError`` when
        writing fails.
        """
        target = path or self._workspace.path
        if target is None:
            raise ValueError("A workspace without a file needs a path to be saved.")
        self._workspace = self._saved_workspace = save_workspace(self._workspace, target)
        self._settle_saved()
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
        removed_ids = {content.content_id for content in self._resolutions.pop(item_id).contents}
        self._cancel_rescan(item_id)
        self._cancel_resolving(item_id)
        self._not_considered = {ref for ref in self._not_considered if ref.content_id not in removed_ids}
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

    def rescan_item(self, item_id: str) -> None:
        """Resolve the item with *item_id* again in the background and replace its result when the new one arrives.

        The new result, an error included, applies only while the item and the Workspace are still current. Exclusions
        of Content that still exists stay, those of Content that is gone are dropped, and newly appeared Content starts
        from its defaults. Rescanning never opens a viewer and never changes what open viewers show, since they keep
        the immutable Content they opened; nor does it make the Workspace modified, as results are never saved. An item
        that is still resolving is left to that resolution. Rescanning an item again before the first rescan finishes
        makes the latest request the one that counts: the earlier one is cancelled, so its result never arrives, and a
        rescan still waiting its turn never runs.
        """
        if not self._workspace.has_item(item_id):
            logger.warning(f"Attempted to rescan an item not in the workspace: {item_id}")
            return
        if self._resolutions[item_id].is_pending:
            return
        self._cancel_rescan(item_id)
        self._rescans[item_id] = self._resolver.resolve(
            self._workspace.item(item_id), partial(self._apply_rescan, self._generation)
        )

    def _cancel_rescan(self, item_id: str) -> None:
        """Cancel the rescan of the item with *item_id* that is still under way, if any."""
        cancel = self._rescans.pop(item_id, None)
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

    def _publish_saved(self, generation: int, loaded: tuple[Workspace, Workspace]) -> None:
        """Take the saved state read for a restored Workspace, unless the Workspace was replaced or saved since."""
        if generation != self._generation or not self._awaiting_saved:
            return
        current, saved = loaded
        self._settle_saved()
        self._workspace = replace(self._workspace, path=current.path)
        self._saved_workspace = saved
        self._announce_state()

    def _settle_saved(self) -> None:
        """Stop waiting for a restored Workspace's saved state, and cancel its background read if still queued."""
        self._awaiting_saved = False
        self._read_saved = None
        cancel, self._cancel_read = self._cancel_read, None
        if cancel is not None:
            cancel()

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
            current = resolution.renamed(waiting.item)
            if current.error is not None:
                logger.warning(f"Could not open {current.item.display_name}: {current.error}")
            self._resolutions[current.item.id] = current
            self._not_considered.update(ref for ref, considered in _defaults(current).items() if not considered)
            committed.append(current)
        if committed:
            self.items_resolved.emit(committed, open_first)

    def _apply_rescan(self, generation: int, resolution: ItemResolution) -> None:
        """Replace an item's result with its rescanned one, unless its Workspace was replaced; a removed item's or a
        superseded rescan was cancelled and never arrives."""
        item_id = resolution.item.id
        if generation != self._generation:
            return
        self._rescans.pop(item_id, None)
        current = resolution.renamed(self._workspace.item(item_id))
        if current.error is not None:
            logger.warning(f"Could not open {current.item.display_name}: {current.error}")
        previous = self._resolutions[item_id]
        self._resolutions[item_id] = current
        self._not_considered = _carried_exclusions(self._not_considered, previous, current)
        self.item_rescanned.emit(current)


class _ResolutionBatch:
    """Publish independent item jobs together in input order; all state belongs to the GUI thread."""

    def __init__(self, items: Sequence[WorkspaceItem], done: Callable[[tuple[ItemResolution, ...]], None]) -> None:
        self._items = tuple(items)
        self._results: dict[str, ItemResolution | None] = dict.fromkeys(item.id for item in items)
        self._cancels: dict[str, Callable[[], None]] = {}
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


def _identities(resolution: ItemResolution) -> dict[ConsiderationItemRef, tuple[object, ...]]:
    """Return what names each consideration item of *resolution*'s Content across rescans, by its ref.

    A ref's entry index shifts when a rescan inserts or removes entries, so an entry is named by its Bookmark Target
    entry instead, and a lane by its index within that entry.
    """
    return {
        consideration.ref: (
            consideration.ref.kind,
            consideration.ref.content_id,
            content.bookmark_entries[consideration.ref.entry_index],
            consideration.ref.lane_index,
        )
        for content in resolution.contents
        for consideration in content.consideration_items()
    }


def _carried_exclusions(
    not_considered: set[ConsiderationItemRef], previous: ItemResolution, current: ItemResolution
) -> set[ConsiderationItemRef]:
    """Return *not_considered* with the exclusions of *previous* moved to the same items of *current*.

    An item that is gone loses its exclusion, and one that is new starts at its default.
    """
    before, after = _identities(previous), _identities(current)
    excluded = {before[ref] for ref in not_considered if ref in before}
    known = set(before.values())
    defaults = _defaults(current)
    return {ref for ref in not_considered if ref not in before} | {
        ref for ref, identity in after.items() if identity in excluded or (identity not in known and not defaults[ref])
    }
