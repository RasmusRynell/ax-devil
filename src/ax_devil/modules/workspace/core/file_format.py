"""Saving and loading Workspace files: JSON, version 1, written atomically."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.items import item_from_json
from ax_devil.modules.workspace.core.workspace import Workspace

logger = get_logger(__name__)

FORMAT_VERSION = 1


class WorkspaceFileError(Exception):
    """A Workspace file could not be read or written; the message is the user-facing reason."""


def _backing_file(path: Path) -> Path:
    """Return the file that holds the Workspace's data: *path* itself, or the file a symlink at *path* points to.

    Relative item paths are anchored to this file's folder, and saving replaces this file, so a symlink keeps
    pointing at the real workspace file instead of being replaced by it.
    """
    return path.resolve() if path.is_symlink() else path.absolute()


def load_workspace(path: Path) -> Workspace:
    """Return the Workspace saved in *path*, with its ``path`` set.

    Items that cannot be read become unreadable items; only a damaged file as a whole raises ``WorkspaceFileError``.
    """
    path = path.absolute()
    backing = _backing_file(path)
    try:
        document = json.loads(backing.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkspaceFileError(f"Could not read {path.name}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise WorkspaceFileError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise WorkspaceFileError(f"{path.name} is not a workspace file.")
    found = document.get("version")
    if type(found) is not int or found != FORMAT_VERSION:
        raise WorkspaceFileError(f"{path.name} has workspace file version {found!r}; this app reads {FORMAT_VERSION}.")
    entries = document.get("items")
    if not isinstance(entries, list):
        raise WorkspaceFileError(f"{path.name} has no list of items.")
    try:
        return Workspace(path=path, items=tuple(item_from_json(entry, backing.parent) for entry in entries))
    except ValueError as exc:
        raise WorkspaceFileError(f"{path.name}: {exc}") from exc


def save_workspace(workspace: Workspace, path: Path) -> Workspace:
    """Write *workspace* to *path* and return it with ``path`` set; the old file is replaced only when complete."""
    path = path.absolute()
    backing = _backing_file(path)
    document: dict[str, Any] = {
        "version": FORMAT_VERSION,
        "items": [item.to_json(backing.parent) for item in workspace.items],
    }
    temporary: Path | None = None
    try:
        backing.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=backing.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(f"{json.dumps(document, indent=2, allow_nan=False)}\n")
        if backing.exists():
            os.chmod(temporary, stat.S_IMODE(backing.stat().st_mode))
        temporary.replace(backing)
    except (OSError, TypeError, ValueError) as exc:
        raise WorkspaceFileError(f"Could not save {path.name}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    logger.info(f"Saved workspace to {path}")
    return replace(workspace, path=path)
