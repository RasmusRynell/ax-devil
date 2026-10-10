"""Workspace session composition and API for the main UI."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    ItemResolution,
    RecentWorkspaces,
    WorkspaceBackup,
    WorkspaceItem,
    video_file_selections,
)
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.application_window import ApplicationWindow
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.sidebar_panel import SidebarPanel
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.start_panel import StartPanel
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_lifecycle import WorkspaceLifecycle
from ax_devil.modules.workspace.ui.workspace_prompts import DialogPrompts, WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
    from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


def _storage_file(name: str) -> Path:
    """Return *name* in the storage folder saved now; a changed folder applies from the next launch."""
    return Path(ConfigManager().get_saved("storage")["base_dir"]) / name


class WorkspaceSession(QObject):
    """Compose the workspace UI around one store and its lifecycle, and tear it down.

    *resolver* resolves items, by default with the installed plugins in the background. *backup*,
    *recent_workspaces*, and *prompts* go to the ``WorkspaceLifecycle``; *prompts* defaults to dialogs over the window.
    """

    state_changed = Signal()  # The Workspace's name or modified flag changed.

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        render_catalog_manager: SceneRenderCatalogManager,
        resolver: ItemResolver | None = None,
        backup: WorkspaceBackup | None = None,
        recent_workspaces: RecentWorkspaces | None = None,
        prompts: WorkspacePrompts | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = get_logger(__name__)
        self._render_catalog_manager = render_catalog_manager
        self._resolver = resolver or ItemResolver(default_resolution_context(), parent=self)

        self._workspace_store = WorkspaceStore(self._resolver, parent=self)
        self._content_browser = ContentBrowserWidget()
        self._start_panel = StartPanel()
        self._sidebar = SidebarPanel(self._content_browser, self._start_panel)
        self._center_area = SplitView()
        self._window = ApplicationWindow(
            sidebar=self._sidebar,
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
        self._lifecycle = WorkspaceLifecycle(
            self._workspace_store,
            prompts or DialogPrompts(self._window),
            backup or WorkspaceBackup(partial(_storage_file, "workspace-backup.json")),
            recent_workspaces or RecentWorkspaces(partial(_storage_file, "recent-workspaces.json")),
            parent=self,
        )
        self._show_recent_workspaces()
        self._lifecycle.recent_workspaces_changed.connect(self._show_recent_workspaces)
        self._start_panel.recent_workspace_requested.connect(self._lifecycle.open_workspace)
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

    @property
    def lifecycle(self) -> WorkspaceLifecycle:
        """Return the owner of launching, quitting, New, Open, Save, and Save As."""
        return self._lifecycle

    @property
    def resolver(self) -> ItemResolver:
        """Return the resolver that resolves this session's items away from the GUI thread."""
        return self._resolver

    def add_items(self, items: Sequence[WorkspaceItem]) -> None:
        """Add *items* to the workspace; once they resolve, the first opens and those that cannot open are reported."""
        self._workspace_store.add_items(items)

    def add_resolved(self, resolutions: Sequence[ItemResolution]) -> None:
        """Add items a dialog already resolved, without resolving them again, and open the first."""
        self._workspace_store.add_resolved(resolutions)

    def _show_recent_workspaces(self) -> None:
        """Show the recent workspace files on the start panel."""
        self._start_panel.set_recent_workspaces(self._lifecycle.recent_workspaces())

    def open_files(self, paths: Sequence[Path]) -> None:
        """Add dropped files as one batch, asking for an overlay's data handler when several decoders may read it."""
        selections = video_file_selections(paths, self._resolver.context.intake)
        if not selections:
            self._logger.warning("No video files to open among the dropped files")
        items: list[WorkspaceItem] = []
        for selection in selections:
            if selection.needs_decoder:
                with AddVideoDialog(self._window, initial=selection) as dialog:
                    dialog.exec()
                    item = dialog.get_result()
                if item is not None:
                    items.append(item)
            else:
                items.append(selection.to_item())
        self.add_items(items)

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

    def set_shortcut_manager(self, manager: ShortcutManager) -> None:
        """Trigger *manager*'s actions from the welcome screen, the start panel, and the activity bar.

        Call once: each call adds the activity bar's action buttons.
        """
        self._center_area.set_welcome_shortcut_manager(manager)
        self._start_panel.set_shortcut_manager(manager)
        activity_bar = self._window.activity_bar()
        for action_id, icon in (("view.render_catalogs", Icon.CATALOGS), ("app.settings", Icon.SETTINGS)):
            activity_bar.add_action(icon, manager.tooltip(action_id), manager.get_action(action_id).trigger)
