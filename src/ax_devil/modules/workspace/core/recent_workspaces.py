"""Recently opened and saved workspace files, newest first."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.file_format import write_json_file

logger = get_logger(__name__)

RECENT_WORKSPACES_LIMIT = 8


@dataclass(frozen=True)
class RecentWorkspace:
    """A remembered workspace file and the local time it was last opened or saved."""

    path: Path
    used: datetime


class RecentWorkspaces:
    """Persist the most recently opened or saved workspace files.

    The file is a JSON list of ``{"path": <absolute path>, "used": <ISO date-time>}``. The time of use is kept here
    so showing a recent never has to read the remembered file, which may be on a slow or disconnected mount.
    """

    def __init__(self, path_provider: Callable[[], Path], limit: int = RECENT_WORKSPACES_LIMIT) -> None:
        self._path_provider = path_provider
        self._limit = limit

    def entries(self) -> tuple[RecentWorkspace, ...]:
        """Return the remembered workspaces, newest first; their files are not checked, so none is touched."""
        return tuple(self._load())

    def record(self, workspace_path: Path) -> None:
        """Put *workspace_path* first, and keep at most the limit.

        The path is kept as the user chose it, made absolute and normalized lexically; symlinks are not followed.
        """
        workspace_path = Path(os.path.normpath(workspace_path.absolute()))
        used = RecentWorkspace(workspace_path, datetime.now())
        self._write([used, *(entry for entry in self.entries() if entry.path != workspace_path)][: self._limit])

    def forget(self, workspace_path: Path) -> None:
        """Remove *workspace_path*, as recorded, from the list; a file that cannot be opened is forgotten this way."""
        workspace_path = Path(os.path.normpath(workspace_path.absolute()))
        self._write([entry for entry in self.entries() if entry.path != workspace_path])

    def _write(self, entries: list[RecentWorkspace]) -> None:
        try:
            document = [{"path": str(entry.path), "used": entry.used.isoformat()} for entry in entries]
            write_json_file(self._path_provider(), document)
        except OSError as exc:
            logger.warning(f"Could not save recent workspaces to {self._path_provider()}: {exc}")

    def _load(self) -> list[RecentWorkspace]:
        if not self._path_provider().is_file():
            return []
        try:
            document = json.loads(self._path_provider().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"Ignoring unreadable recent workspaces file {self._path_provider()}: {exc}")
            return []
        if not isinstance(document, list):
            return []
        entries = []
        for item in document:
            try:
                entries.append(RecentWorkspace(Path(item["path"]), datetime.fromisoformat(item["used"])))
            except (TypeError, KeyError, ValueError):
                continue
        return entries
