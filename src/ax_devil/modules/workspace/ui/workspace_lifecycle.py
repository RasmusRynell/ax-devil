"""What launching, quitting, New, Open, Save, and Save As do to the current Workspace."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    RecentWorkspace,
    RecentWorkspaces,
    Workspace,
    WorkspaceFileError,
    WorkspaceItem,
    save_workspace,
    workspace_file_path,
)
from ax_devil.modules.workspace.core.legacy_backup import LEGACY_BACKUP_FILE, read_legacy_backup
from ax_devil.modules.workspace.ui.workspace_prompts import SaveChoice, WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

logger = get_logger(__name__)

_COPY_STEM_BYTES = 150  # Leaves room for the date, a number, and the extension within a file name's 255 bytes.


class WorkspaceLifecycle(QObject):
    """Decide when the Workspace in *store* is replaced, saved, kept, and remembered; build no widgets.

    Launching starts with an empty Workspace. Quitting never asks; a Workspace with unsaved edits is saved as a copy
    in the folder *unsaved_folder* names and joins *recent_workspaces* like any opened or saved file. Starting a new
    Workspace asks before discarding edits; opening a saved Workspace discards them. The first launch after an upgrade
    keeps the edits of the ``workspace-backup.json`` that earlier versions left beside *unsaved_folder* the same way.
    """

    recent_workspaces_changed = Signal()

    def __init__(
        self,
        store: WorkspaceStore,
        prompts: WorkspacePrompts,
        unsaved_folder: Callable[[], Path],
        recent_workspaces: RecentWorkspaces,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._prompts = prompts
        self._unsaved_folder = unsaved_folder
        self._recent_workspaces = recent_workspaces

    def recent_workspaces(self) -> tuple[RecentWorkspace, ...]:
        """Return the recently opened or saved workspace files, newest first; their files are not checked."""
        return self._recent_workspaces.entries()

    def launch(self, items: Sequence[WorkspaceItem] = (), workspace_file: Path | None = None) -> None:
        """Open *workspace_file* or start a new Workspace with *items*, if given; otherwise stay empty."""
        self._keep_legacy_backup()
        if workspace_file is not None:
            self.open_workspace(workspace_file)
        elif items:
            self.new_workspace(items)

    def keep_unsaved_changes(self) -> None:
        """Save a Workspace with unsaved edits as a copy and list it in the recent workspaces; call when quitting.

        The Workspace's own file is not touched. A failure is logged, since quitting must not be stopped by it.
        """
        if not self._store.is_modified:
            return
        copy = self._write_copy(self._store.workspace)
        if copy is not None:
            self._recent_workspaces.record(copy)  # Unlisted, the copy is still on disk if this fails.

    def new_workspace(self, items: Sequence[WorkspaceItem] = ()) -> bool:
        """Replace the Workspace with a new Untitled one holding *items*; return False when the user cancels."""
        if not self._settle_unsaved_changes():
            return False
        self._store.replace_workspace(Workspace())
        self._store.add_items(items)
        return True

    def open_workspace(self, path: Path | None = None) -> bool:
        """Open the workspace saved in *path*, or in a file the user picks, discarding current edits."""
        path = path or self._prompts.choose_workspace_to_open()
        if path is None:
            return False
        try:
            self._store.open_workspace(path)
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(f"Could not open workspace {path}: {exc}")
            self._recent_workspaces.forget(path)
            self.recent_workspaces_changed.emit()
            self._prompts.show_error(str(exc))
            return False
        self._remember(path)
        return True

    def save_workspace(self) -> bool:
        """Save the Workspace to its file, or ask where when it has none; return whether it was saved."""
        path = self._store.workspace.path
        return self.save_workspace_as() if path is None else self._save_to(path)

    def save_workspace_as(self) -> bool:
        """Save the Workspace to a file the user picks; return whether it was saved.

        The extension is appended when the chosen name lacks it; the dialog did not check that file, so replacing it
        is asked here.
        """
        workspace = self._store.workspace
        chosen = self._prompts.choose_save_path(
            workspace.path or workspace_file_path(Path.home() / workspace.suggested_file_stem)
        )
        if chosen is None:
            return False
        path = workspace_file_path(chosen)
        if path != chosen and path.exists() and not self._prompts.confirm_replace(path):
            return False
        return self._save_to(path)

    def _save_to(self, path: Path) -> bool:
        """Save the Workspace to *path*, telling the user when that fails."""
        try:
            self._store.save_workspace(path)
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(f"Could not save workspace to {path}: {exc}")
            self._prompts.show_error(str(exc))
            return False
        self._remember(path)
        return True

    def _settle_unsaved_changes(self) -> bool:
        """Return whether the current Workspace may be replaced, asking to save it first when it is modified."""
        if not self._store.is_modified:
            return True
        choice = self._prompts.ask_save_changes(self._store.workspace.name)
        return choice is SaveChoice.DISCARD or (choice is SaveChoice.SAVE and self.save_workspace())

    def _write_copy(self, workspace: Workspace) -> Path | None:
        """Save *workspace* as a new file in the unsaved folder; return its path, or None when that failed."""
        copy: Path | None = None
        try:
            copy = self._reserve_copy_path(workspace)
            save_workspace(workspace, copy)
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(
                f"Could not keep the unsaved workspace {workspace.name!r} in {self._unsaved_folder()}: {exc}"
            )
            if copy is not None:
                copy.unlink(missing_ok=True)
            return None
        return copy

    def _reserve_copy_path(self, workspace: Workspace) -> Path:
        """Create and return an empty file named after *workspace* and now that no other file or instance has.

        The name is cut to a length every file system accepts. Creating the file is what claims the name, so instances
        saving at the same moment get different files.
        """
        folder = self._unsaved_folder()
        folder.mkdir(parents=True, exist_ok=True)
        stem = workspace.suggested_file_stem.encode()[:_COPY_STEM_BYTES].decode(errors="ignore")
        stem = f"{stem} {datetime.now():%Y-%m-%d %H-%M-%S}"
        number = 1
        while True:
            path = workspace_file_path(folder / (stem if number == 1 else f"{stem} {number}"))
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                number += 1
            else:
                return path

    def _keep_legacy_backup(self) -> None:
        """Keep the unsaved edits in an earlier version's backup as a recent workspace, then delete the backup.

        A backup that is damaged or is the empty Untitled Workspace is deleted without a copy; one that cannot be read
        now, or whose copy cannot be saved and listed, stays for the next launch. The backup's saved file is not read,
        so the copy is made whether or not the Workspace has unsaved edits.
        """
        backup = self._unsaved_folder().parent / LEGACY_BACKUP_FILE
        if not backup.is_file():
            return
        try:
            unsaved = read_legacy_backup(backup)
        except OSError as exc:
            logger.warning(f"Could not read the earlier version's workspace backup {backup}, keeping it: {exc}")
            return
        if unsaved is not None:
            copy = self._write_copy(unsaved)
            if copy is None:
                return
            if not self._recent_workspaces.record(copy):
                copy.unlink(missing_ok=True)  # The backup is the one copy that stays, so there is no duplicate.
                return
        try:
            backup.unlink()
        except OSError as exc:
            logger.warning(f"Could not delete the earlier version's workspace backup {backup}: {exc}")
        self.recent_workspaces_changed.emit()

    def _remember(self, path: Path) -> None:
        """Record *path* as the most recent workspace file."""
        self._recent_workspaces.record(path)
        self.recent_workspaces_changed.emit()
