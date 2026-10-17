"""Reading the ``workspace-backup.json`` that earlier versions kept on quit, so its unsaved edits are not lost."""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.file_format import (
    FORMAT_VERSION,
    WorkspaceFileError,
    load_workspace,
    write_json_file,
)
from ax_devil.modules.workspace.core.workspace import Workspace

logger = get_logger(__name__)

LEGACY_BACKUP_FILE = "workspace-backup.json"
"""The earlier versions' file in the storage folder."""


def read_legacy_backup(backup: Path) -> Workspace | None:
    """Return the Workspace kept in *backup* unless it is the empty Untitled Workspace, else None.

    The file is a workspace file without a version and with the path of the workspace's own file or null, and with
    absolute item paths. That workspace file is never read, since it may be on a slow or disconnected mount, so a
    backup of an unchanged saved Workspace is returned like any other. A file whose content is damaged is logged and
    gives None. Raises ``OSError`` when the file cannot be read now, which says nothing about its content.
    """
    text = backup.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError("it is not a JSON object")
        document.pop("path", None)
        document["version"] = FORMAT_VERSION
        with tempfile.TemporaryDirectory() as folder:
            converted = Path(folder) / "workspace"
            write_json_file(converted, document)
            kept = load_workspace(converted)
    except (ValueError, WorkspaceFileError) as exc:
        logger.warning(f"Ignoring damaged workspace backup {backup}: {exc}")
        return None
    kept = replace(kept, path=None)
    return None if kept == Workspace() else kept
