"""The current Workspace, what its items resolved to, and exclusions, with Qt change signals."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
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
    Workspace is modified or what it is called.
    """

    items_added = Signal(list)  # list[WorkspaceItem]; they may still be resolving
    items_resolved = Signal(list, bool)  # list[ItemResolution], and whether the user added the items
    item_removed = Signal(object)  # WorkspaceItem
    item_renamed = Signal(object)  # WorkspaceItem, with its new label
    item_consideration_changed = Signal(object, bool)
    workspace_replaced = Signal()  # another Workspace was opened, created, or restored; every item may be new
    state_changed = Signal()  # is_modified, the name, or the path changed

    def __init__(self, resolver: ItemResolver, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._resolver = resolver
        self._workspace = Workspace()
        self._saved_workspace = self._workspace
        self._resolutions: dict[str, ItemResolution] = {}
        self._not_considered: set[ConsiderationItemRef] = set()
        self._generation = 0  # counts replaced Workspaces, so results for an earlier one are dropped
        self._announced_state = self._state()

    @property
    def workspace(self) -> Workspace:
        """Return the current Workspace."""
        return self._workspace

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
        self.workspace_replaced.emit()
        self._announce_state()
        self._resolve(current.items, added=False)

    def save_workspace(self, path: Path | None = None) -> None:
        """Save the Workspace to *path*, or to its own file when None, and mark it as saved.

        Raises ``ValueError`` for an Untitled Workspace without a *path*, and ``WorkspaceFileError`` when writing fails.
        """
        target = path or self._workspace.path
        if target is None:
            raise ValueError("An Untitled workspace needs a path to be saved.")
        self._workspace = self._saved_workspace = save_workspace(self._workspace, target)
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
        """Remove the item with *item_id*, its Content, and its exclusions."""
        if not self._workspace.has_item(item_id):
            logger.warning(f"Attempted to remove an item not in the workspace: {item_id}")
            return
        item = self._workspace.item(item_id)
        self._workspace = self._workspace.remove_item(item_id)
        removed_ids = {content.content_id for content in self._resolutions.pop(item_id).contents}
        self._not_considered = {ref for ref in self._not_considered if ref.content_id not in removed_ids}
        logger.debug(f"Item removed: {item.display_name} ({item_id})")
        self.item_removed.emit(item)
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

    def _append(self, items: Sequence[WorkspaceItem]) -> None:
        """Append *items*, each pending until its result is committed."""
        if not items:
            return
        self._workspace = self._workspace.add_items(items)
        self._resolutions.update((item.id, ItemResolution.pending(item)) for item in items)
        self.items_added.emit(list(items))
        self._announce_state()

    def _resolve(self, items: Sequence[WorkspaceItem], *, added: bool) -> None:
        """Resolve *items* in the background and commit the results to the Workspace they belong to.

        Added items resolve together, so the first of them is the one that opens; a replacing Workspace's items resolve
        one by one, so a slow item never holds back the others.
        """
        batches = [items] if added else [[item] for item in items]
        for batch in batches:
            if batch:
                self._resolver.resolve(batch, partial(self._commit, self._generation, added=added))

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
            self._not_considered.update(
                consideration.ref
                for content in current.contents
                for consideration in content.consideration_items()
                if not consideration.default_considered
            )
            committed.append(current)
        if committed:
            self.items_resolved.emit(committed, added)
