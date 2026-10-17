"""Saving and loading Workspace files: JSON, version 1, written atomically."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.bookmarks import bookmarks_from_json, bookmarks_to_json
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


def _reject_constant(token: str) -> NoReturn:
    """Refuse ``NaN``, ``Infinity`` and ``-Infinity``: Python accepts them when reading, but they cannot be saved."""
    raise ValueError(f"{token} is not allowed in JSON")


def _read_document(backing: Path, name: str) -> dict[str, Any]:
    """Return the JSON object in *backing*, called *name* in messages; anything else raises ``WorkspaceFileError``."""
    try:
        document = json.loads(backing.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except OSError as exc:
        raise WorkspaceFileError(f"Could not read {name}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise WorkspaceFileError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise WorkspaceFileError(f"{name} is not a workspace file.")
    return document


def _workspace_from_document(document: dict[str, Any], path: Path, base_dir: Path) -> Workspace:
    """Return the Workspace *document* holds, with ``path`` set and item paths read relative to *base_dir*."""
    found = document.get("version")
    if type(found) is not int or found != FORMAT_VERSION:
        raise WorkspaceFileError(f"{path.name} has workspace file version {found!r}; this app reads {FORMAT_VERSION}.")
    name = document.get("name")
    if not isinstance(name, str):
        raise WorkspaceFileError(f"{path.name} has no name.")
    entries = document.get("items")
    if not isinstance(entries, list):
        raise WorkspaceFileError(f"{path.name} has no list of items.")
    try:
        bookmarks, reviewed = bookmarks_from_json(document)
        items = tuple(item_from_json(entry, base_dir) for entry in entries)
        return Workspace(name=name, path=path, items=items, bookmarks=bookmarks, reviewed=reviewed)
    except ValueError as exc:
        raise WorkspaceFileError(f"{path.name}: {exc}") from exc


def _document_of(workspace: Workspace, base_dir: Path) -> dict[str, Any]:
    """Return the JSON document for *workspace*, with item paths inside *base_dir* relative to it."""
    return {
        "version": FORMAT_VERSION,
        "name": workspace.name,
        "items": [item.to_json(base_dir) for item in workspace.items],
        **bookmarks_to_json(workspace.bookmarks, workspace.reviewed),
    }


def load_workspace(path: Path) -> Workspace:
    """Return the Workspace saved in *path*, with its ``path`` set.

    Items that cannot be read become unreadable items. A damaged file as a whole, or a malformed bookmark or reviewed
    target, raises ``WorkspaceFileError``, so bookmarks are never dropped silently.
    """
    path = path.absolute()
    backing = _backing_file(path)
    return _workspace_from_document(_read_document(backing, path.name), path, backing.parent)


def save_workspace(workspace: Workspace, path: Path) -> Workspace:
    """Write *workspace* to *path* and return it with ``path`` set; the old file is replaced only when complete."""
    path = path.absolute()
    backing = _backing_file(path)
    try:
        write_json_file(backing, _document_of(workspace, backing.parent))
    except (OSError, TypeError, ValueError) as exc:
        raise WorkspaceFileError(f"Could not save {path.name}: {exc}") from exc
    logger.info(f"Saved workspace to {path}")
    return replace(workspace, path=path)


def write_recovery(workspace: Workspace, path: Path) -> None:
    """Write *workspace* to the recovery copy *path*, replacing the old copy only when complete.

    A recovery copy is a workspace file that also names the file it recovers, ``workspace_file``: the Workspace's own
    path, or null for an Untitled one. Nothing is logged, since a copy is rewritten as the Workspace changes.
    """
    path = path.absolute()
    backing = _backing_file(path)
    document = _document_of(workspace, backing.parent)
    document["workspace_file"] = None if workspace.path is None else str(workspace.path)
    try:
        write_json_file(backing, document)
    except (OSError, TypeError, ValueError) as exc:
        raise WorkspaceFileError(f"Could not save {path.name}: {exc}") from exc


def read_recovery(path: Path) -> Workspace:
    """Return the Workspace kept in the recovery copy *path*, whose ``path`` is the file it recovers.

    That is None for an Untitled Workspace, which a copy without ``workspace_file`` also reads as. The recovered file
    is not read. Raises ``WorkspaceFileError`` like ``load_workspace``.
    """
    path = path.absolute()
    backing = _backing_file(path)
    document = _read_document(backing, path.name)
    recovered = _workspace_from_document(document, path, backing.parent)
    original = document.get("workspace_file")
    if original is None:
        return replace(recovered, path=None)
    if not isinstance(original, str) or not Path(original).is_absolute():
        raise WorkspaceFileError(f"{path.name} has no valid workspace file path.")
    return replace(recovered, path=Path(original))


def write_json_file(path: Path, document: Any) -> None:
    """Write *document* to *path* as indented JSON, replacing the old file only when the new one is complete.

    Raises ``OSError`` when writing fails, and ``TypeError`` or ``ValueError`` when *document* is not JSON.
    """
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(f"{json.dumps(document, indent=2, allow_nan=False)}\n")
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
