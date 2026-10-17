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
    """Return the Workspace kept in *backup* when it holds unsaved edits, else None.

    The file is a workspace file without a version and with the path of the workspace's own file or null, and with
    absolute item paths. There are no edits when it equals that file's content, or the empty Untitled Workspace
    without a file. A file whose content is damaged is logged and also gives None. Raises ``OSError`` when the file
    cannot be read now, which says nothing about its content.
    """
    text = backup.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError("it is not a JSON object")
        saved_file = document.pop("path", None)
        document["version"] = FORMAT_VERSION
        with tempfile.TemporaryDirectory() as folder:
            converted = Path(folder) / "workspace"
            write_json_file(converted, document)
            kept = load_workspace(converted)
    except (ValueError, WorkspaceFileError) as exc:
        logger.warning(f"Ignoring damaged workspace backup {backup}: {exc}")
        return None
    try:
        saved = load_workspace(Path(saved_file)) if isinstance(saved_file, str) else Workspace()
    except WorkspaceFileError:
        saved = Workspace()  # Its file is gone or damaged, so the backup is the only copy.
    kept = replace(kept, path=None)
    return None if kept == replace(saved, path=None) else kept
