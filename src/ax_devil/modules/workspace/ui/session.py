"""Workspace session composition and API for the main UI."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from ax_devil.modules.background_jobs import BackgroundJobs, background_jobs
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.sidebar_view import SidebarView
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.workspace.core import (
    ItemResolution,
    RecentWorkspaces,
    VideoFileSelection,
    VideoItem,
    WorkspaceItem,
    video_file_selections,
)
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.add_content.add_playlist_dialog import AddPlaylistDialog
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.application_window import ApplicationWindow
from ax_devil.modules.workspace.ui.bookmarks_view import BOOKMARKS_VIEW_ID, BookmarksView
from ax_devil.modules.workspace.ui.bookmarks_view_controller import BookmarksViewController
from ax_devil.modules.workspace.ui.browser_rows import HELD_SECTIONS
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.live_view import LiveView
from ax_devil.modules.workspace.ui.live_view_controller import LiveViewController
from ax_devil.modules.workspace.ui.offline_view import OfflineView
from ax_devil.modules.workspace.ui.offline_view_controller import OfflineViewController
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.sections_view_controller import SectionsViewController
from ax_devil.modules.workspace.ui.sidebar_panel import SidebarPanel
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.start_panel import StartPanel
from ax_devil.modules.workspace.ui.viewer_widget import SHARED_ACTION_IDS
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_lifecycle import WorkspaceLifecycle
from ax_devil.modules.workspace.ui.workspace_prompts import DialogPrompts, WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from ax_devil.modules.workspace.ui.workspace_view import WorkspaceView

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


def _storage_file(name: str) -> Path:
    """Return *name* in the storage folder saved now; a changed folder applies from the next launch."""
    return Path(ConfigManager().get_saved("storage")["base_dir"]) / name


class WorkspaceSession(QObject):
    """Compose the workspace UI around one store and its lifecycle, and tear it down.

    *resolver* resolves items, by default with the installed plugins through *jobs*, the application's background jobs
    unless given, which also run network scans. *unsaved_folder*,
    *recent_workspaces*, and *prompts* go to the ``WorkspaceLifecycle``; *prompts* defaults to dialogs over the window.
    """

    state_changed = Signal()  # The Workspace's name or modified flag changed.

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        render_catalog_manager: SceneRenderCatalogManager,
        resolver: ItemResolver | None = None,
        jobs: BackgroundJobs | None = None,
        unsaved_folder: Path | None = None,
        recent_workspaces: RecentWorkspaces | None = None,
        prompts: WorkspacePrompts | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = get_logger(__name__)
        self._render_catalog_manager = render_catalog_manager
        self._resolver = resolver or ItemResolver(default_resolution_context(), parent=self, jobs=jobs)

        self._workspace_store = WorkspaceStore(self._resolver, parent=self)
        self._start_panel = StartPanel()
        self._workspace_view = WorkspaceView(self._start_panel)
        self._center_area = SplitView()
        self._window = ApplicationWindow(
            sidebar=SidebarPanel(),
            center_area=self._center_area,
            parent=parent,
        )
        self._window.add_view(self._workspace_view.sidebar_view())
        self._workspace_controller = WorkspaceController(
            workspace_store=self._workspace_store,
            center_area=self._center_area,
            render_catalog_manager=self._render_catalog_manager,
            parent=self._window,
        )
        self._workspace_listing = SectionsViewController(
            self._workspace_view, self._workspace_controller, HELD_SECTIONS
        )
        self._live_view = LiveView()
        self._live_view_controller = LiveViewController(
            self._live_view, self._workspace_controller, self._workspace_store, jobs or background_jobs(), parent=self
        )
        self._window.add_view(self._live_view.sidebar_view())
        self._live_view.add_manually_requested.connect(self.add_live_stream)
        self._offline_view = OfflineView()
        self._window.add_view(self._offline_view.sidebar_view())
        self._offline_controller = OfflineViewController(self._offline_view, self._workspace_controller, parent=self)
        self._offline_controller.add_requested.connect(self.add_playlist)
        self._offline_view.add_video_requested.connect(self.add_video)
        self._bookmarks_view = BookmarksView()
        self._bookmarks = BookmarksViewController(
            self._bookmarks_view, self._workspace_store, self._workspace_controller, parent=self
        )
        self._window.add_view(self._bookmarks_view.sidebar_view())
        self._lifecycle = WorkspaceLifecycle(
            self._workspace_store,
            prompts or DialogPrompts(self._window),
            (lambda: unsaved_folder) if unsaved_folder is not None else partial(_storage_file, "unsaved-workspaces"),
            recent_workspaces or RecentWorkspaces(partial(_storage_file, "recent-workspaces.json")),
            parent=self,
        )
        self.refresh_recent_workspaces()
        self._lifecycle.recent_workspaces_changed.connect(self.refresh_recent_workspaces)
        self._start_panel.recent_workspace_requested.connect(self._lifecycle.open_workspace)
        self._window.files_dropped.connect(self.open_files)
        self._workspace_store.state_changed.connect(self.state_changed)
        self._workspace_store.state_changed.connect(self._show_workspace_state)
        self._workspace_view.title_edited.connect(self._workspace_store.rename_workspace)
        self._show_workspace_state()

    def _show_workspace_state(self) -> None:
        workspace = self._workspace_store.workspace
        self._workspace_view.set_workspace(workspace.name, workspace.path, self._workspace_store.is_modified)

    def widget(self) -> ApplicationWindow:
        """Return the composed viewer widget tree."""
        return self._window

    @property
    def workspace_name(self) -> str:
        """Return the current Workspace's name, "Untitled" until it is named."""
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

    def add_sidebar_view(self, view: SidebarView) -> None:
        """Add a view to the sidebar, with its button on the activity bar."""
        self._window.add_view(view)

    def show_sidebar_view(self, view_id: str) -> None:
        """Show the sidebar with the view *view_id* on screen."""
        self._window.show_view(view_id)

    def bookmark_focused_viewer(self) -> None:
        """Bookmark the frame on screen in the focused viewer, and show the Bookmarks view.

        Nothing happens without a focused viewer, or when what it shows cannot be bookmarked, such as a live stream.
        Focus stays where it is, so playback keys keep reaching the viewer.
        """
        widget = self.focused_widget()
        position = widget.bookmark_position() if widget is not None else None
        if position is not None and self._bookmarks.add_bookmark(position) is not None:
            self.show_sidebar_view(BOOKMARKS_VIEW_ID)

    def add_items(self, items: Sequence[WorkspaceItem]) -> None:
        """Add *items* to the workspace; once they resolve, the first opens and those that cannot open are reported."""
        self._workspace_store.add_items(items)

    def add_resolved(self, resolutions: Sequence[ItemResolution], *, open_first: bool = True) -> None:
        """Add items a dialog already resolved, without resolving them again, and open the first unless *open_first*
        is False."""
        self._workspace_store.add_resolved(resolutions, open_first=open_first)

    def refresh_scan_defaults(self) -> None:
        """Give the Live view the Settings' default IP range, unless the user typed another, and sign-in defaults."""
        self._live_view_controller.refresh_defaults()

    def scan_network_on_startup(self) -> None:
        """Start the optional background scan, retaining its results in the Live view."""
        if GlobalSettings().scan_on_startup:
            self._live_view_controller.scan(self._live_view.scan_range(), low_impact=True)

    def refresh_recent_workspaces(self) -> None:
        """Show the recent workspace files on the start panel."""
        self._start_panel.set_recent_workspaces(self._lifecycle.recent_workspaces())

    def add_video(self, initial: VideoFileSelection | None = None) -> None:
        """Ask for a video, prefilled from *initial*, and add it."""
        item = self._ask_video(initial)
        if item is not None:
            self.add_items([item])

    def add_live_stream(self) -> None:
        """Ask for a camera's details and add it without starting its stream; Start stream does that."""
        with AddLiveStreamDialog(self._window) as dialog:
            if dialog.exec() == AddLiveStreamDialog.DialogCode.Accepted:
                result = dialog.get_result()
                if result is not None:
                    self.add_resolved([result], open_first=False)
                    self._logger.info(f"Added live stream: {result.item.display_name}")

    def add_playlist(self, resolver_id: str | None = None) -> None:
        """Ask for a playlist's settings, with the resolver *resolver_id* chosen when given, and add it."""
        with AddPlaylistDialog(self._resolver, self._window, resolver_id=resolver_id) as dialog:
            if dialog.exec() == AddPlaylistDialog.DialogCode.Accepted:
                result = dialog.get_result()
                if result is not None:
                    self.add_resolved([result])
                    self._logger.info(f"Added playlist: {result.item.display_name}")

    def open_files(self, paths: Sequence[Path]) -> None:
        """Add dropped files as one batch, asking for an overlay's data handler when several decoders may read it."""
        selections = video_file_selections(paths, self._resolver.context.intake)
        if not selections:
            self._logger.warning("No video files to open among the dropped files")
        items: list[WorkspaceItem] = []
        for selection in selections:
            item = self._ask_video(selection) if selection.needs_decoder else selection.to_item()
            if item is not None:
                items.append(item)
        self.add_items(items)

    def _ask_video(self, initial: VideoFileSelection | None) -> VideoItem | None:
        with AddVideoDialog(self._window, initial=initial) as dialog:
            dialog.exec()
            return dialog.get_result()

    def focused_widget(self) -> ViewerWidget | None:
        """Return the currently focused viewer widget, regardless of type."""
        return self._center_area.get_focused_widget()

    def cleanup(self) -> None:
        """Release workspace viewers and related UI state."""
        self._live_view_controller.cleanup()
        self._workspace_controller.cleanup()
        self._logger.debug("Workspace session cleanup completed")

    def set_shortcut_manager(self, manager: ShortcutManager) -> None:
        """Trigger *manager*'s actions from the welcome screen, the start panel, and the activity bar, and list some of
        them in the viewers' menus.

        Call once: each call adds the activity bar's action buttons.
        """
        self._center_area.set_welcome_shortcut_manager(manager)
        self._start_panel.set_shortcut_manager(manager)
        self._workspace_controller.set_shared_actions(
            {action_id: manager.get_action(action_id) for action_id in SHARED_ACTION_IDS}
        )
        activity_bar = self._window.activity_bar()
        for action_id, icon in (("view.render_catalogs", Icon.CATALOGS), ("app.settings", Icon.SETTINGS)):
            button = activity_bar.add_action(icon, "", manager.get_action(action_id).trigger)
            manager.bind_tooltip(button, action_id)
