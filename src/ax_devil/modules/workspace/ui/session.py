"""Workspace session composition and API for the main UI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    RecentWorkspaces,
    ResolutionContext,
    Workspace,
    WorkspaceBackup,
    WorkspaceFileError,
    WorkspaceItem,
    video_file_selections,
    workspace_file_path,
)
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.application_window import ApplicationWindow
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_prompts import DialogPrompts, SaveChoice, WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
    from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


def _storage_file(name: str) -> Path:
    """Return the file called *name* in the configured application storage directory."""
    return Path(ConfigManager().get("storage")["base_dir"]) / name


class WorkspaceSession(QObject):
    """Own workspace state, workspace UI composition, the workspace file lifecycle, and teardown.

    The lifecycle works like VS Code: closing never asks and keeps the current Workspace in *backup*; launching restores
    it. Replacing a modified Workspace asks Save / Discard / Cancel first. Opened and saved files join
    *recent_workspaces*. *prompts* asks the user; it defaults to dialogs over the window.
    """

    state_changed = Signal()  # The Workspace's name or modified flag changed.

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        render_catalog_manager: SceneRenderCatalogManager,
        context: ResolutionContext | None = None,
        backup: WorkspaceBackup | None = None,
        recent_workspaces: RecentWorkspaces | None = None,
        prompts: WorkspacePrompts | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = get_logger(__name__)
        self._render_catalog_manager = render_catalog_manager
        self._context = context or default_resolution_context()
        self._backup = backup or WorkspaceBackup(_storage_file("workspace-backup.json"))
        self._recent_workspaces = recent_workspaces or RecentWorkspaces(_storage_file("recent-workspaces.json"))

        self._workspace_store = WorkspaceStore(self._context, parent=self)
        self._content_browser = ContentBrowserWidget()
        self._center_area = SplitView()
        self._window = ApplicationWindow(
            content_browser=self._content_browser,
            center_area=self._center_area,
            parent=parent,
        )
        self._workspace_controller = WorkspaceController(
            workspace_store=self._workspace_store,
            content_browser=self._content_browser,
            center_area=self._center_area,
            render_catalog_manager=self._render_catalog_manager,
            parent=self._window,
        )
        self._prompts = prompts or DialogPrompts(self._window)
        self._welcome = self._center_area.welcome_widget()
        self._welcome.set_recent_workspaces(self._recent_workspaces.entries())
        self._welcome.recent_workspace_requested.connect(self.open_workspace)
        self._window.files_dropped.connect(self.open_files)
        self._workspace_store.state_changed.connect(self.state_changed)

    def widget(self) -> ApplicationWindow:
        """Return the composed viewer widget tree."""
        return self._window

    @property
    def workspace_name(self) -> str:
        """Return the current Workspace's name: its file name without the extension, or "Untitled"."""
        return self._workspace_store.workspace.name

    @property
    def is_modified(self) -> bool:
        """Return whether the current Workspace has unsaved changes."""
        return self._workspace_store.is_modified

    def recent_workspaces(self) -> tuple[Path, ...]:
        """Return the recently opened or saved workspace files that still exist, newest first."""
        return self._recent_workspaces.entries()

    def add_items(self, items: Sequence[WorkspaceItem]) -> None:
        """Add *items* to the workspace and open the first; items that cannot open are kept and reported."""
        self._workspace_store.add_items(items)

    def launch(self, items: Sequence[WorkspaceItem] = (), workspace_file: Path | None = None) -> None:
        """Restore the kept Workspace, then open *workspace_file* or start a new Workspace with *items*, if given.

        The restored Workspace lists its items and opens nothing. When the user cancels replacing it, it stays.
        """
        self._workspace_store.replace_workspace(*self._backup.restore())
        if workspace_file is not None:
            self.open_workspace(workspace_file)
        elif items and not self.new_workspace(items):
            self._logger.info(f"Kept the restored workspace; {len(items)} item(s) from the command line were not added")

    def keep_workspace(self) -> None:
        """Keep the current Workspace, unsaved edits included, for the next launch."""
        self._backup.keep(self._workspace_store.workspace)

    def new_workspace(self, items: Sequence[WorkspaceItem] = ()) -> bool:
        """Replace the Workspace with a new Untitled one holding *items*; return False when the user cancels."""
        if not self._settle_unsaved_changes():
            return False
        self._workspace_store.replace_workspace(Workspace())
        self._workspace_store.add_items(items)
        return True

    def open_workspace(self, path: Path | None = None) -> bool:
        """Open the workspace saved in *path*, or in a file the user picks; return whether it opened."""
        path = path or self._prompts.choose_workspace_to_open()
        if path is None or not self._settle_unsaved_changes():
            return False
        try:
            self._workspace_store.open_workspace(path)
        except (WorkspaceFileError, OSError) as exc:
            self._logger.warning(f"Could not open workspace {path}: {exc}")
            self._prompts.show_error(str(exc))
            return False
        self._remember(path)
        return True

    def save_workspace(self) -> bool:
        """Save the Workspace to its file, or ask where when it is Untitled; return whether it was saved."""
        path = self._workspace_store.workspace.path
        return self.save_workspace_as() if path is None else self._save_to(path)

    def save_workspace_as(self) -> bool:
        """Save the Workspace to a file the user picks; return whether it was saved.

        The extension is appended when the chosen name lacks it; the dialog did not check that file, so replacing it
        is asked here.
        """
        workspace = self._workspace_store.workspace
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
            self._workspace_store.save_workspace(path)
        except (WorkspaceFileError, OSError) as exc:
            self._logger.warning(f"Could not save workspace to {path}: {exc}")
            self._prompts.show_error(str(exc))
            return False
        self._remember(path)
        return True

    def _settle_unsaved_changes(self) -> bool:
        """Return whether the current Workspace may be replaced, asking to save it first when it is modified."""
        if not self._workspace_store.is_modified:
            return True
        choice = self._prompts.ask_save_changes(self._workspace_store.workspace.name)
        return choice is SaveChoice.DISCARD or (choice is SaveChoice.SAVE and self.save_workspace())

    def _remember(self, path: Path) -> None:
        """Record *path* as the most recent workspace file and show the list on the welcome screen."""
        self._recent_workspaces.record(path)
        self._welcome.set_recent_workspaces(self._recent_workspaces.entries())

    def open_files(self, paths: Sequence[Path]) -> None:
        """Open dropped files, asking for the overlay's data handler when more than one decoder may read it."""
        selections = video_file_selections(paths, self._context.intake)
        if not selections:
            self._logger.warning("No video files to open among the dropped files")
        for selection in selections:
            if selection.needs_decoder:
                with AddVideoDialog(self._window, initial=selection) as dialog:
                    dialog.exec()
                    item = dialog.get_result()
                if item is None:
                    continue
            else:
                item = selection.to_item()
            self.add_items([item])

    def focused_offline_viewer(self) -> OfflineVideoViewerWidget | None:
        """Return the currently focused offline viewer, if any."""
        from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget

        widget = self._center_area.get_focused_widget()
        if isinstance(widget, OfflineVideoViewerWidget):
            return widget
        return None

    def focused_widget(self) -> ViewerWidget | None:
        """Return the currently focused viewer widget, regardless of type."""
        return self._center_area.get_focused_widget()

    def cleanup(self) -> None:
        """Release workspace viewers and related UI state."""
        self._workspace_controller.cleanup()
        self._logger.debug("Workspace session cleanup completed")

    def set_welcome_shortcut_manager(self, manager: ShortcutManager) -> None:
        """Configure the welcome screen to read shortcuts from *manager*."""
        self._center_area.set_welcome_shortcut_manager(manager)
