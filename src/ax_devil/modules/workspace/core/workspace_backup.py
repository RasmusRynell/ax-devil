"""The workspace kept between launches: its file and its current items, including unsaved edits."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.file_format import WorkspaceFileError, load_workspace, write_json_file
from ax_devil.modules.workspace.core.items import item_from_json
from ax_devil.modules.workspace.core.workspace import Workspace

logger = get_logger(__name__)


class WorkspaceBackup:
    """Keep the current Workspace in one JSON file: its file path, or null, and its items with absolute paths.

    Items use the workspace file's item JSON, so unreadable items survive a restart unchanged.
    """

    def __init__(self, path_provider: Callable[[], Path]) -> None:
        self._path_provider = path_provider

    def keep(self, workspace: Workspace) -> None:
        """Write *workspace* to the backup file; a failure is logged, since closing must not be stopped by it."""
        document = {
            "path": str(workspace.path) if workspace.path is not None else None,
            "items": [item.to_json(None) for item in workspace.items],
        }
        try:
            write_json_file(self._path_provider(), document)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"Could not keep the workspace in {self._path_provider()}: {exc}")

    def restore(self) -> tuple[Workspace, Workspace]:
        """Return the kept Workspace and its last saved state, as ``(current, saved)``.

        The saved state is the kept file's content, or the empty Untitled Workspace when the workspace was never saved.
        When that file can no longer be read, the kept items stay as an Untitled Workspace. Without a usable backup,
        both are the empty Untitled Workspace.
        """
        empty = Workspace()
        current = self._read()
        if current is None:
            return empty, empty
        if current.path is None:
            return current, empty
        try:
            return current, load_workspace(current.path)
        except WorkspaceFileError as exc:
            logger.warning(f"Restoring the kept workspace as Untitled: {exc}")
            return replace(current, path=None), empty

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
            return Workspace(
                path=Path(path) if isinstance(path, str) else None,
                items=tuple(item_from_json(entry, None) for entry in entries),
            )
        except ValueError as exc:
            logger.warning(f"Ignoring workspace backup {self._path_provider()}: {exc}")
            return None
