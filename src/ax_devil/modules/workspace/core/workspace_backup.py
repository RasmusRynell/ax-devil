"""The workspace kept between launches: its file and its current items, including unsaved edits."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.bookmarks import bookmarks_from_json, bookmarks_to_json
from ax_devil.modules.workspace.core.file_format import WorkspaceFileError, load_workspace, write_json_file
from ax_devil.modules.workspace.core.items import item_from_json
from ax_devil.modules.workspace.core.workspace import Workspace

logger = get_logger(__name__)


class WorkspaceBackup:
    """Keep the current Workspace in one JSON file: its file path, or null, its items with absolute paths, and its
    bookmarks and reviewed targets.

    Items and bookmarks use the workspace file's JSON, so unreadable items survive a restart unchanged.
    """

    def __init__(self, path_provider: Callable[[], Path]) -> None:
        self._path_provider = path_provider

    def keep(self, workspace: Workspace) -> None:
        """Write *workspace* to the backup file; a failure is logged, since closing must not be stopped by it."""
        document = {
            "path": str(workspace.path) if workspace.path is not None else None,
            "items": [item.to_json(None) for item in workspace.items],
            **bookmarks_to_json(workspace.bookmarks, workspace.reviewed),
        }
        try:
            write_json_file(self._path_provider(), document)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"Could not keep the workspace in {self._path_provider()}: {exc}")

    def restore(self) -> Workspace:
        """Return the kept Workspace, or the empty Untitled Workspace without a usable backup.

        Only the backup file is read; ``load_saved`` reads the kept workspace file, which may be slow to reach.
        """
        return self._read() or Workspace()

    @staticmethod
    def load_saved(kept: Workspace) -> tuple[Workspace, Workspace]:
        """Return *kept* and its last saved state, as ``(current, saved)``; this reads the kept workspace file.

        The saved state is the file's content, or the empty Untitled Workspace when *kept* was never saved. When that
        file can no longer be read, the kept items stay as an Untitled Workspace.
        """
        empty = Workspace()
        if kept.path is None:
            return kept, empty
        try:
            return kept, load_workspace(kept.path)
        except WorkspaceFileError as exc:
            logger.warning(f"Restoring the kept workspace as Untitled: {exc}")
            return replace(kept, path=None), empty

    def _read(self) -> Workspace | None:
        """Return the kept Workspace, or None when there is no backup or it cannot be read."""
        if not self._path_provider().is_file():
            return None
        try:
            document = json.loads(self._path_provider().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"Ignoring unreadable workspace backup {self._path_provider()}: {exc}")
            return None
        entries = document.get("items") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            logger.warning(f"Ignoring workspace backup without a list of items: {self._path_provider()}")
            return None
        path = document.get("path")
        try:
            bookmarks, reviewed = bookmarks_from_json(document)
            return Workspace(
                path=Path(path) if isinstance(path, str) else None,
                items=tuple(item_from_json(entry, None) for entry in entries),
                bookmarks=bookmarks,
                reviewed=reviewed,
            )
        except ValueError as exc:
            logger.warning(f"Ignoring workspace backup {self._path_provider()}: {exc}")
            return None
