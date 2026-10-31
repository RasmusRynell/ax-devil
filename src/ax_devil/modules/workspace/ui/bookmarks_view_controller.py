"""Feed the Bookmarks view from the workspace and carry out what the user asks of it."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject

from ax_devil.modules.chrome.choice_dialog import notify
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import Bookmark, BookmarkKind, BookmarkPosition
from ax_devil.modules.workspace.core.bookmark_csv import write_bookmarks_csv
from ax_devil.modules.workspace.ui.bookmark_rows import BookmarkGroup, build_bookmark_groups, filter_bookmark_groups
from ax_devil.modules.workspace.ui.bookmarks_view import BookmarksView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

logger = get_logger(__name__)


class BookmarksViewController(QObject):
    """Keep *view* showing the store's bookmarks as the workspace changes, filtered as the user asks.

    Opening a bookmark shows its frame, paused, through *workspace_controller*: a viewer already showing the target
    seeks there, and otherwise the target opens as a double click does; the bookmark opened last is marked until the
    bookmarks change.
    """

    def __init__(
        self,
        view: BookmarksView,
        store: WorkspaceStore,
        workspace_controller: WorkspaceController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._view = view
        self._store = store
        self._workspace_controller = workspace_controller
        self._groups: tuple[BookmarkGroup, ...] = ()
        self._query = ""
        self._kind: BookmarkKind | None = None
        self._active_id: str | None = None
        store.bookmarks_changed.connect(self._on_bookmarks_changed)
        workspace_controller.sections_changed.connect(self._on_sections_changed)
        view.search_changed.connect(self._on_search_changed)
        view.kind_filter_changed.connect(self._on_kind_filter_changed)
        view.bookmark_activated.connect(self.open_bookmark)
        view.reviewed_change_requested.connect(store.set_reviewed)
        view.rename_requested.connect(self.rename_bookmark)
        view.kind_change_requested.connect(lambda bookmark_id, kind: store.edit_bookmark(bookmark_id, kind=kind))
        view.remove_requested.connect(store.remove_bookmark)
        view.export_requested.connect(self._on_export_requested)
        self._rebuild()
        self._show()

    def add_bookmark(self, position: BookmarkPosition) -> Bookmark | None:
        """Bookmark the frame at *position* as a note without text; return it, or None when it cannot be bookmarked."""
        return self._store.add_bookmark(position.target, position.frame, position.time_s)

    def open_bookmark(self, bookmark_id: str) -> None:
        """Open the target of the bookmark *bookmark_id* at its frame; an orphaned target opens nothing."""
        for group in self._groups:
            for row in group.rows:
                if row.bookmark.id == bookmark_id and group.found is not None:
                    content, entry_index = group.found
                    self._active_id = bookmark_id
                    self._workspace_controller.show_frame(content, entry_index, row.bookmark.frame)
                    self._show()
                    return

    def rename_bookmark(self, bookmark_id: str, name: str) -> None:
        """Make *name* the bookmark's note; an empty name clears the note."""
        self._store.edit_bookmark(bookmark_id, note=name)

    def export_csv(self, path: Path) -> None:
        """Write every bookmark to *path* as CSV, in the order the view lists them; report a failure to the user."""
        try:
            write_bookmarks_csv(path, (row for group in self._groups for row in group.csv_rows()))
        except OSError as exc:
            logger.warning(f"Could not export bookmarks to {path}: {exc}")
            notify(self._view, "Export CSV", f"Could not write {path}: {exc}")

    def _on_bookmarks_changed(self) -> None:
        self._active_id = None
        self._rebuild()
        self._show()

    def _on_sections_changed(self, *_args: object) -> None:
        """Show what the targets resolve to now; most row changes, such as a viewer stepping entries, change nothing."""
        if self._rebuild():
            self._show()

    def _on_search_changed(self, query: str) -> None:
        self._query = query
        self._show()

    def _on_kind_filter_changed(self, kind: BookmarkKind | None) -> None:
        self._kind = kind
        self._show()

    def _on_export_requested(self) -> None:
        workspace = self._store.workspace
        folder = workspace.path.parent if workspace.path is not None else Path.home()
        path = self._view.choose_export_path(folder / f"{workspace.suggested_file_stem} bookmarks.csv")
        if path is not None:
            self.export_csv(path)

    def _rebuild(self) -> bool:
        """Build the groups from the store again, and return whether they changed."""
        groups = build_bookmark_groups(self._store.resolutions(), self._store.bookmarks, self._store.reviewed)
        changed = groups != self._groups
        self._groups = groups
        return changed

    def _show(self) -> None:
        count = sum(len(group.rows) for group in self._groups)
        self._view.set_groups(filter_bookmark_groups(self._groups, self._query, self._kind), count, self._active_id)
