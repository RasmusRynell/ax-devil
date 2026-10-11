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
    items_resolved = Signal(list, bool)  # list[ItemResolution], and whether the user added the items
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
        self._resolutions: dict[str, ItemResolution] = {}
        self._not_considered: set[ConsiderationItemRef] = set()
        self._generation = 0  # counts replaced Workspaces, so results for an earlier one are dropped
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
        self._awaiting_saved = False
        self._read_saved = None
        self.workspace_replaced.emit()
        self.bookmarks_changed.emit()
        self._announce_state()
        self._resolve(current.items, added=False)

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
        self._resolver.run(
            self._read_saved,
            partial(self._publish_saved, generation),
            lambda: generation == self._generation and self._awaiting_saved,
        )

    def finish_restore(self) -> None:
        """Read the saved state of a restored Workspace now, on the calling thread, when it is still pending.

        Call it before a decision that needs the real saved state, such as asking about unsaved edits; it blocks as
        long as the file read takes. The background result that arrives later is dropped.
        """
        if self._awaiting_saved and self._read_saved is not None:
            self._publish_saved(self._generation, self._read_saved())

    def save_workspace(self, path: Path | None = None) -> None:
        """Save the Workspace to *path*, or to its own file when None, and mark it as saved.

        Raises ``ValueError`` for an Untitled Workspace without a *path*, and ``WorkspaceFileError`` when writing fails.
        """
        target = path or self._workspace.path
        if target is None:
            raise ValueError("An Untitled workspace needs a path to be saved.")
        self._workspace = self._saved_workspace = save_workspace(self._workspace, target)
        self._awaiting_saved = False
        self._read_saved = None
        self._announce_state()

    def add_items(self, items: Sequence[WorkspaceItem]) -> None:
        """Append *items* and resolve them in the background; an item that fails to resolve stays, with its error."""
        self._append(items)
        self._resolve(items, added=True)

    def add_resolved(self, resolutions: Sequence[ItemResolution]) -> None:
        """Append items a dialog already resolved, with their results, so they are not resolved again."""
        self._append([resolution.item for resolution in resolutions])
        self._commit(self._generation, tuple(resolutions), added=True)

    def remove_item(self, item_id: str) -> None:
        """Remove the item with *item_id*, its Content, its exclusions, and its bookmarks and reviewed targets."""
        if not self._workspace.has_item(item_id):
            logger.warning(f"Attempted to remove an item not in the workspace: {item_id}")
            return
        item = self._workspace.item(item_id)
        before = self._workspace
        self._workspace = self._workspace.remove_item(item_id)
        removed_ids = {content.content_id for content in self._resolutions.pop(item_id).contents}
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
        """Mark *target* as reviewed or not; a target whose item is missing or not bookmarkable is ignored."""
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
        that is still resolving is left to that resolution.
        """
        if not self._workspace.has_item(item_id):
            logger.warning(f"Attempted to rescan an item not in the workspace: {item_id}")
            return
        if self._resolutions[item_id].is_pending:
            return
        generation = self._generation
        self._resolver.resolve(
            [self._workspace.item(item_id)],
            partial(self._apply_rescan, generation),
            lambda: generation == self._generation and self._workspace.has_item(item_id),
        )

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
        self._awaiting_saved = False
        self._read_saved = None
        self._workspace = replace(self._workspace, path=current.path)
        self._saved_workspace = saved
        self._announce_state()

    def _resolve(self, items: Sequence[WorkspaceItem], *, added: bool) -> None:
        """Resolve *items* in the background and commit the results to the Workspace they belong to.

        Added items resolve together, so the first of them is the one that opens; a replacing Workspace's items resolve
        one by one, so a slow item never holds back the others.
        """
        generation = self._generation
        batches = [items] if added else [[item] for item in items]
        for batch in batches:
            if batch:
                self._resolver.resolve(
                    batch, partial(self._commit, generation, added=added), lambda: generation == self._generation
                )

    def _commit(self, generation: int, resolutions: tuple[ItemResolution, ...], *, added: bool) -> None:
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
            self.items_resolved.emit(committed, added)

    def _apply_rescan(self, generation: int, resolutions: tuple[ItemResolution, ...]) -> None:
        """Replace an item's result with its rescanned one, unless the item or its Workspace is gone."""
        [resolution] = resolutions
        item_id = resolution.item.id
        if generation != self._generation or not self._workspace.has_item(item_id):
            return
        current = resolution.renamed(self._workspace.item(item_id))
        if current.error is not None:
            logger.warning(f"Could not open {current.item.display_name}: {current.error}")
        before, after = _defaults(self._resolutions[item_id]), _defaults(current)
        self._resolutions[item_id] = current
        self._not_considered = {ref for ref in self._not_considered if ref not in before or ref in after}
        self._not_considered.update(ref for ref, considered in after.items() if ref not in before and not considered)
        self.item_rescanned.emit(current)


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
