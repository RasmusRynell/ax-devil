"""The Workspace: an immutable, ordered collection of Workspace Items, with their bookmarks and review marks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ax_devil.modules.workspace.core.bookmarks import Bookmark, BookmarkKind, BookmarkTarget
from ax_devil.modules.workspace.core.items import WorkspaceItem

UNTITLED_NAME = "Untitled"
WORKSPACE_FILE_SUFFIX = ".ax-devil.workspace"


def workspace_file_path(path: Path) -> Path:
    """Return *path* with the workspace file extension appended when it does not end with it already."""
    return path if path.name.endswith(WORKSPACE_FILE_SUFFIX) else path.with_name(f"{path.name}{WORKSPACE_FILE_SUFFIX}")


def workspace_name(path: Path | None) -> str:
    """Return the name of the workspace saved at *path*: the file name without its extension, or "Untitled"."""
    return path.name.removesuffix(WORKSPACE_FILE_SUFFIX) if path is not None else UNTITLED_NAME


@dataclass(frozen=True)
class Workspace:
    """The items a user works with, and the file they are saved in; ``path`` is None until the first save.

    Edits return a new value. Two workspaces are equal when their paths, items, bookmarks, and reviewed targets are, so
    a store can tell unsaved edits by comparing the current value with the last saved one. Items may hold mappings, so
    do not hash workspaces.

    Bookmarks and reviewed targets refer to items in the Workspace, and removing an item removes them. Only a
    ``bookmarkable`` item takes new ones or review changes; ones a file already holds load whatever the item, so a
    bookmark on an item this version cannot read is kept and saved back.
    """

    path: Path | None = None
    items: tuple[WorkspaceItem, ...] = ()
    bookmarks: tuple[Bookmark, ...] = ()
    reviewed: frozenset[BookmarkTarget] = frozenset()

    def __post_init__(self) -> None:
        """Reject two items or two bookmarks with the same id, and bookmarks or reviewed targets of missing items."""
        ids = [item.id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("Workspace items must have unique ids.")
        bookmark_ids = [bookmark.id for bookmark in self.bookmarks]
        if len(set(bookmark_ids)) != len(bookmark_ids):
            raise ValueError("Bookmarks must have unique ids.")
        targets = {bookmark.target for bookmark in self.bookmarks} | self.reviewed
        missing = sorted({target.item_id for target in targets} - set(ids))
        if missing:
            raise ValueError(f"Bookmarks or reviewed targets refer to items not in the workspace: {', '.join(missing)}")

    @property
    def name(self) -> str:
        """Return the file name without the workspace extension, or "Untitled" before the first save."""
        return workspace_name(self.path)

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
        """Return this workspace without the item with *item_id* and its bookmarks and reviewed targets.

        Raises ``KeyError`` when there is no such item.
        """
        self.item(item_id)
        return replace(
            self,
            items=tuple(item for item in self.items if item.id != item_id),
            bookmarks=tuple(bookmark for bookmark in self.bookmarks if bookmark.target.item_id != item_id),
            reviewed=frozenset(target for target in self.reviewed if target.item_id != item_id),
        )

    def rename_item(self, item_id: str, label: str) -> Workspace:
        """Return this workspace with the item with *item_id* renamed to *label*, or raise ``KeyError``."""
        self.item(item_id)
        return replace(self, items=tuple(item.with_label(label) if item.id == item_id else item for item in self.items))

    def bookmark(self, bookmark_id: str) -> Bookmark:
        """Return the bookmark with *bookmark_id*, or raise ``KeyError``."""
        for bookmark in self.bookmarks:
            if bookmark.id == bookmark_id:
                return bookmark
        raise KeyError(bookmark_id)

    def add_bookmark(self, bookmark: Bookmark) -> Workspace:
        """Return this workspace with *bookmark* appended.

        Raises ``ValueError`` when its item is missing or not bookmarkable, or its id is already in use.
        """
        self._check_bookmarkable(bookmark.target)
        return replace(self, bookmarks=(*self.bookmarks, bookmark))

    def remove_bookmark(self, bookmark_id: str) -> Workspace:
        """Return this workspace without the bookmark with *bookmark_id*, or raise ``KeyError``."""
        self.bookmark(bookmark_id)
        return replace(self, bookmarks=tuple(bookmark for bookmark in self.bookmarks if bookmark.id != bookmark_id))

    def edit_bookmark(
        self, bookmark_id: str, *, note: str | None = None, kind: BookmarkKind | None = None
    ) -> Workspace:
        """Return this workspace with the bookmark's note and kind replaced where given, or raise ``KeyError``."""
        current = self.bookmark(bookmark_id)
        edited = replace(
            current, note=current.note if note is None else note, kind=current.kind if kind is None else kind
        )
        return replace(
            self, bookmarks=tuple(edited if bookmark.id == bookmark_id else bookmark for bookmark in self.bookmarks)
        )

    def set_reviewed(self, target: BookmarkTarget, reviewed: bool) -> Workspace:
        """Return this workspace with *target* marked reviewed or not.

        Raises ``ValueError`` when the target's item is missing or not bookmarkable.
        """
        self._check_bookmarkable(target)
        return replace(self, reviewed=self.reviewed | {target} if reviewed else self.reviewed - {target})

    def _check_bookmarkable(self, target: BookmarkTarget) -> None:
        """Raise ``ValueError`` unless *target*'s item is in this workspace and takes bookmarks."""
        if not self.has_item(target.item_id):
            raise ValueError(f"No item {target.item_id} in the workspace.")
        item = self.item(target.item_id)
        if not item.bookmarkable:
            raise ValueError(f"{item.display_name} cannot take bookmarks.")
