"""What launching, quitting, New, Open, Save, and Save As do to the current Workspace."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    RecentWorkspaces,
    Workspace,
    WorkspaceBackup,
    WorkspaceFileError,
    WorkspaceItem,
    workspace_file_path,
)
from ax_devil.modules.workspace.ui.workspace_prompts import SaveChoice, WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

logger = get_logger(__name__)


class WorkspaceLifecycle(QObject):
    """Decide when the Workspace in *store* is replaced, saved, kept, and remembered; build no widgets.

    The lifecycle works like VS Code: quitting never asks and keeps the current Workspace in *backup*; launching
    restores it. Replacing a modified Workspace asks Save / Discard / Cancel through *prompts* first. Opened and saved
    files join *recent_workspaces*.
    """

    recent_workspaces_changed = Signal()

    def __init__(
        self,
        store: WorkspaceStore,
        prompts: WorkspacePrompts,
        backup: WorkspaceBackup,
        recent_workspaces: RecentWorkspaces,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._prompts = prompts
        self._backup = backup
        self._recent_workspaces = recent_workspaces

    def recent_workspaces(self) -> tuple[Path, ...]:
        """Return the recently opened or saved workspace files that still exist, newest first."""
        return self._recent_workspaces.entries()

    def launch(self, items: Sequence[WorkspaceItem] = (), workspace_file: Path | None = None) -> None:
        """Restore the kept Workspace, then open *workspace_file* or start a new Workspace with *items*, if given.

        The restored Workspace lists its items and opens nothing. When the user cancels replacing it, it stays.
        """
        self._store.replace_workspace(*self._backup.restore())
        if workspace_file is not None:
            self.open_workspace(workspace_file)
        elif items and not self.new_workspace(items):
            logger.info(f"Kept the restored workspace; {len(items)} item(s) from the command line were not added")

    def keep_workspace(self) -> None:
        """Keep the current Workspace, unsaved edits included, for the next launch."""
        self._backup.keep(self._store.workspace)

    def new_workspace(self, items: Sequence[WorkspaceItem] = ()) -> bool:
        """Replace the Workspace with a new Untitled one holding *items*; return False when the user cancels."""
        if not self._settle_unsaved_changes():
            return False
        self._store.replace_workspace(Workspace())
        self._store.add_items(items)
        return True

    def open_workspace(self, path: Path | None = None) -> bool:
        """Open the workspace saved in *path*, or in a file the user picks; return whether it opened."""
        path = path or self._prompts.choose_workspace_to_open()
        if path is None or not self._settle_unsaved_changes():
            return False
        try:
            self._store.open_workspace(path)
        except (WorkspaceFileError, OSError) as exc:
            logger.warning(f"Could not open workspace {path}: {exc}")
            self._prompts.show_error(str(exc))
            return False
        self._remember(path)
        return True

    def save_workspace(self) -> bool:
        """Save the Workspace to its file, or ask where when it is Untitled; return whether it was saved."""
        path = self._store.workspace.path
        return self.save_workspace_as() if path is None else self._save_to(path)

    def save_workspace_as(self) -> bool:
        """Save the Workspace to a file the user picks; return whether it was saved.

        The extension is appended when the chosen name lacks it; the dialog did not check that file, so replacing it
        is asked here.
        """
        workspace = self._store.workspace
        chosen = self._prompts.choose_save_path(workspace.path or workspace_file_path(Path.home() / workspace.name))
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

    def _remember(self, path: Path) -> None:
        """Record *path* as the most recent workspace file."""
        self._recent_workspaces.record(path)
        self.recent_workspaces_changed.emit()
