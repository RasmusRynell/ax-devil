"""The questions and file choices the workspace lifecycle asks the user."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from ax_devil.modules.chrome.choice_dialog import ask
from ax_devil.modules.workspace.core import WORKSPACE_FILE_SUFFIX

_FILE_FILTER = f"Workspaces (*{WORKSPACE_FILE_SUFFIX})"


class WorkspacePrompts(Protocol):
    """Ask the user about workspace files; tests replace the dialogs with fixed answers."""

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
        return ask(
            self._parent, "Save Workspace As", f"Replace {path.name}?", {"Replace": True, "Cancel": False}, False
        )

    def show_error(self, message: str) -> None:
        """Show *message* as a warning."""
        QMessageBox.warning(self._parent, "Workspace", message)
