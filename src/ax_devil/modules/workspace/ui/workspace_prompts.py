"""The questions and file choices the workspace lifecycle asks the user."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Protocol

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from ax_devil.modules.workspace.core import WORKSPACE_FILE_SUFFIX

_FILE_FILTER = f"Workspaces (*{WORKSPACE_FILE_SUFFIX})"


class SaveChoice(Enum):
    """The answer to whether unsaved changes should be saved before they are replaced."""

    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"


class WorkspacePrompts(Protocol):
    """Ask the user about workspace files; tests replace the dialogs with fixed answers."""

    def ask_save_changes(self, workspace_name: str) -> SaveChoice:
        """Return whether to save the modified workspace before it is replaced."""
        ...

    def choose_workspace_to_open(self) -> Path | None:
        """Return the workspace file to open, or None when the user cancels."""
        ...

    def choose_save_path(self, suggested: Path) -> Path | None:
        """Return where to save the workspace, starting at *suggested*, or None when the user cancels."""
        ...

    def confirm_replace(self, path: Path) -> bool:
        """Return whether to replace the existing *path*, which the save dialog did not ask about."""
        ...

    def show_error(self, message: str) -> None:
        """Tell the user why a workspace file could not be opened or saved."""
        ...


class DialogPrompts:
    """Workspace prompts shown as Qt message boxes and file dialogs over *parent*."""

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent

    def ask_save_changes(self, workspace_name: str) -> SaveChoice:
        """Ask Save / Discard / Cancel about the modified workspace."""
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self._parent,
            "Save Workspace",
            f"Save changes to {workspace_name}?",
            buttons.Save | buttons.Discard | buttons.Cancel,
            buttons.Save,
        )
        return {buttons.Save: SaveChoice.SAVE, buttons.Discard: SaveChoice.DISCARD}.get(answer, SaveChoice.CANCEL)

    def choose_workspace_to_open(self) -> Path | None:
        """Show an open dialog filtered to workspace files."""
        path, _ = QFileDialog.getOpenFileName(self._parent, "Open Workspace", "", _FILE_FILTER)
        return Path(path) if path else None

    def choose_save_path(self, suggested: Path) -> Path | None:
        """Show a save dialog filtered to workspace files."""
        path, _ = QFileDialog.getSaveFileName(self._parent, "Save Workspace As", str(suggested), _FILE_FILTER)
        return Path(path) if path else None

    def confirm_replace(self, path: Path) -> bool:
        """Ask whether to replace *path*."""
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self._parent, "Save Workspace As", f"Replace {path.name}?", buttons.Yes | buttons.No, buttons.No
        )
        return answer == buttons.Yes

    def show_error(self, message: str) -> None:
        """Show *message* as a warning."""
        QMessageBox.warning(self._parent, "Workspace", message)
