"""The Workspace: an immutable, ordered collection of Workspace Items."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ax_devil.modules.workspace.core.items import WorkspaceItem

UNTITLED_NAME = "Untitled"
WORKSPACE_FILE_SUFFIX = ".ax-devil.workspace"


@dataclass(frozen=True)
class Workspace:
    """The items a user works with, and the file they are saved in; ``path`` is None until the first save.

    Edits return a new value. Two workspaces are equal when their paths and items are, so a store can tell unsaved
    edits by comparing the current value with the last saved one. Items may hold mappings, so do not hash workspaces.
    """

    path: Path | None = None
    items: tuple[WorkspaceItem, ...] = ()

    def __post_init__(self) -> None:
        """Reject two items with the same id."""
        ids = [item.id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("Workspace items must have unique ids.")

    @property
    def name(self) -> str:
        """Return the file name without the workspace extension, or "Untitled" before the first save."""
        return self.path.name.removesuffix(WORKSPACE_FILE_SUFFIX) if self.path is not None else UNTITLED_NAME

    def has_item(self, item_id: str) -> bool:
        """Return whether an item with *item_id* is in this workspace."""
        return any(item.id == item_id for item in self.items)

    def item(self, item_id: str) -> WorkspaceItem:
        """Return the item with *item_id*, or raise ``KeyError``."""
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(item_id)

    def add_items(self, items: Sequence[WorkspaceItem]) -> Workspace:
        """Return this workspace with *items* appended."""
        return replace(self, items=(*self.items, *items))

    def remove_item(self, item_id: str) -> Workspace:
        """Return this workspace without the item with *item_id*, or raise ``KeyError``."""
        self.item(item_id)
        return replace(self, items=tuple(item for item in self.items if item.id != item_id))

    def rename_item(self, item_id: str, label: str) -> Workspace:
        """Return this workspace with the item with *item_id* renamed to *label*, or raise ``KeyError``."""
        self.item(item_id)
        return replace(self, items=tuple(item.with_label(label) if item.id == item_id else item for item in self.items))
