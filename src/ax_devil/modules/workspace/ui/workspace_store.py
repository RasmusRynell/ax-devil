"""The current Workspace, what its items resolved to, and exclusions, with Qt change signals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    Content,
    ItemResolutionError,
    ResolutionContext,
    Workspace,
    WorkspaceItem,
    load_workspace,
    save_workspace,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ItemResolution:
    """What one item resolved to: its Content, or the error that kept it from resolving."""

    contents: tuple[Content, ...] = ()
    error: ItemResolutionError | None = None
    base: tuple[Content, ...] = ()  # Content before the label is applied; a rename names this, never resolves again.


class WorkspaceStore(QObject):
    """Own the current and last saved Workspace, each item's resolution, and exclusions.

    Items are the truth; their Content is resolved when they are added or renamed. Exclusions are session state and
    are never part of the Workspace. Every successful mutation emits exactly one signal about the items,
    plus ``state_changed`` when it changes whether the Workspace is modified or what it is called.
    """

    items_added = Signal(list)  # list[WorkspaceItem]
    item_removed = Signal(object)  # WorkspaceItem
    item_renamed = Signal(object)  # WorkspaceItem, with its new label
    item_consideration_changed = Signal(object, bool)
    workspace_replaced = Signal()  # another Workspace was opened; every item may be new
    state_changed = Signal()  # is_modified, the name, or the path changed

    def __init__(self, context: ResolutionContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._workspace = Workspace()
        self._saved_workspace = self._workspace
        self._resolutions: dict[str, ItemResolution] = {}
        self._not_considered: set[ConsiderationItemRef] = set()
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
        opened = load_workspace(path)
        self._workspace = self._saved_workspace = opened
        self._resolutions = {}
        self._not_considered = set()
        for item in opened.items:
            self._resolve(item)
        self.workspace_replaced.emit()
        self._announce_state()

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
        """Append and resolve *items*; an item that fails to resolve stays, with its error recorded."""
        if not items:
            return
        self._workspace = self._workspace.add_items(items)
        for item in items:
            self._resolve(item)
        self.items_added.emit(list(items))
        self._announce_state()

    def remove_item(self, item_id: str) -> None:
        """Remove the item with *item_id*, its Content, and its exclusions."""
        if not self._workspace.has_item(item_id):
            logger.warning(f"Attempted to remove an item not in the workspace: {item_id}")
            return
        item = self._workspace.item(item_id)
        self._workspace = self._workspace.remove_item(item_id)
        removed_ids = {content.content_id for content in self._resolutions.pop(item_id).contents}
        self._not_considered = {ref for ref in self._not_considered if ref.content_id not in removed_ids}
        logger.debug(f"Item removed: {item.label} ({item_id})")
        self.item_removed.emit(item)
        self._announce_state()

    def rename_item(self, item_id: str, label: str) -> None:
        """Relabel the item with *item_id* and name its kept Content again; nothing is resolved.

        Content ids, errors, and exclusions stay as they were.
        """
        if not self._workspace.has_item(item_id):
            logger.warning(f"Attempted to rename an item not in the workspace: {item_id}")
            return
        self._workspace = self._workspace.rename_item(item_id, label)
        item = self._workspace.item(item_id)
        previous = self._resolutions[item_id]
        self._resolutions[item_id] = ItemResolution(
            contents=item.name_contents(previous.base), error=previous.error, base=previous.base
        )
        self.item_renamed.emit(item)
        self._announce_state()

    def contents(self) -> tuple[Content, ...]:
        """Return the Content of every resolved item, in item order."""
        return tuple(content for item in self._workspace.items for content in self._resolutions[item.id].contents)

    def resolution(self, item_id: str) -> ItemResolution:
        """Return what the item with *item_id* resolved to."""
        return self._resolutions[item_id]

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

    def _resolve(self, item: WorkspaceItem) -> None:
        """Resolve *item*, record the result, and exclude the Content whose default is not considered."""
        try:
            base = item.resolve_base(self._context)
            resolution = ItemResolution(contents=item.name_contents(base), base=base)
        except ItemResolutionError as exc:
            logger.warning(f"Could not open {item.label}: {exc}")
            resolution = ItemResolution(error=exc)
        self._resolutions[item.id] = resolution
        self._not_considered.update(
            consideration.ref
            for content in resolution.contents
            for consideration in content.consideration_items()
            if not consideration.default_considered
        )
