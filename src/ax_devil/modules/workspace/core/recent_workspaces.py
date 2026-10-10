"""Recently opened and saved workspace files, newest first."""

from __future__ import annotations

import json
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.file_format import write_json_file

logger = get_logger(__name__)

RECENT_WORKSPACES_LIMIT = 8


class RecentWorkspaces:
    """Persist the most recently opened or saved workspace files as a JSON list of absolute paths."""

    def __init__(self, path: Path, limit: int = RECENT_WORKSPACES_LIMIT) -> None:
        self._path = path
        self._limit = limit

    def entries(self) -> tuple[Path, ...]:
        """Return remembered workspace files that still exist, newest first."""
        return tuple(path for path in self._load() if path.is_file())

    def record(self, workspace_path: Path) -> None:
        """Put *workspace_path* first, drop files that no longer exist, and keep at most the limit."""
        workspace_path = workspace_path.resolve()
        entries = [workspace_path, *(path for path in self.entries() if path != workspace_path)][: self._limit]
        try:
            write_json_file(self._path, [str(path) for path in entries])
        except OSError as exc:
            logger.warning(f"Could not save recent workspaces to {self._path}: {exc}")

    def _load(self) -> list[Path]:
        if not self._path.is_file():
            return []
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"Ignoring unreadable recent workspaces file {self._path}: {exc}")
            return []
        return [Path(entry) for entry in document if isinstance(entry, str)] if isinstance(document, list) else []
