"""Recently opened and saved workspace files, newest first."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.file_format import write_json_file

logger = get_logger(__name__)

RECENT_WORKSPACES_LIMIT = 8


class RecentWorkspaces:
    """Persist the most recently opened or saved workspace files as a JSON list of absolute paths."""

    def __init__(self, path_provider: Callable[[], Path], limit: int = RECENT_WORKSPACES_LIMIT) -> None:
        self._path_provider = path_provider
        self._limit = limit

    def entries(self) -> tuple[Path, ...]:
        """Return the remembered workspace files, newest first; their files are not checked, so none is touched."""
        return tuple(self._load())

    def record(self, workspace_path: Path) -> None:
        """Put *workspace_path* first, and keep at most the limit.

        The path is kept as the user chose it, made absolute and normalized lexically; symlinks are not followed.
        """
        workspace_path = Path(os.path.normpath(workspace_path.absolute()))
        entries = [workspace_path, *(path for path in self.entries() if path != workspace_path)][: self._limit]
        self._write(entries)

    def forget(self, workspace_path: Path) -> None:
        """Remove *workspace_path*, as recorded, from the list; a file that cannot be opened is forgotten this way."""
        workspace_path = Path(os.path.normpath(workspace_path.absolute()))
        self._write([path for path in self.entries() if path != workspace_path])

    def _write(self, entries: list[Path]) -> None:
        try:
            write_json_file(self._path_provider(), [str(path) for path in entries])
        except OSError as exc:
            logger.warning(f"Could not save recent workspaces to {self._path_provider()}: {exc}")

    def _load(self) -> list[Path]:
        if not self._path_provider().is_file():
            return []
        try:
            document = json.loads(self._path_provider().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"Ignoring unreadable recent workspaces file {self._path_provider()}: {exc}")
            return []
        return [Path(entry) for entry in document if isinstance(entry, str)] if isinstance(document, list) else []
