"""Workspace session composition and API for the main UI."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    ResolutionContext,
    VideoItem,
    WorkspaceItem,
    new_item_id,
    video_file_selections,
)
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.application_window import ApplicationWindow
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.recent_videos import RecentVideos, default_recent_videos
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
    from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


class WorkspaceSession(QObject):
    """Own workspace state, workspace UI composition, and teardown."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        render_catalog_manager: SceneRenderCatalogManager,
        context: ResolutionContext | None = None,
        recent_videos: RecentVideos | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = get_logger(__name__)
        self._render_catalog_manager = render_catalog_manager
        self._context = context or default_resolution_context()
        self._recent_videos = recent_videos or default_recent_videos()

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
        self._welcome = self._center_area.welcome_widget()
        self._welcome.set_recent_videos(self._recent_videos.entries())
        self._welcome.recent_video_requested.connect(self._open_recent_video)
        self._window.files_dropped.connect(self.open_files)

    def widget(self) -> ApplicationWindow:
        """Return the composed viewer widget tree."""
        return self._window

    def add_items(self, items: Sequence[WorkspaceItem]) -> None:
        """Add *items* to the workspace; items that cannot open are kept and reported."""
        self._workspace_store.add_items(items)

    def open_video(self, item: VideoItem) -> None:
        """Add a Video Item and, when it opens, remember it in the welcome screen's recent list."""
        self._workspace_store.add_items([item])
        if self._workspace_store.resolution(item.id).error is not None:
            return
        self._recent_videos.record(item)
        self._welcome.set_recent_videos(self._recent_videos.entries())

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
            self.open_video(item)

    def _open_recent_video(self, entry: VideoItem) -> None:
        """Open a recent video as a new item, so opening the same entry twice adds two items."""
        self.open_video(replace(entry, id=new_item_id()))

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
