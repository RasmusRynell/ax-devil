"""Workspace session composition and API for the main UI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.application_window import ApplicationWindow
from ax_devil.modules.workspace.content import Content
from ax_devil.modules.workspace.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.intake import WorkspaceIntake, default_workspace_intake
from ax_devil.modules.workspace.recent_videos import RecentVideos, default_recent_videos
from ax_devil.modules.workspace.session_controller import WorkspaceController
from ax_devil.modules.workspace.split_view import SplitView
from ax_devil.modules.workspace.startup_request import (
    StartupContent,
    VideoFileStartup,
    video_file_requests,
)
from ax_devil.modules.workspace.workspace_manager import WorkspaceManager

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
    from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
    from ax_devil.modules.workspace.viewer_host import WorkspaceWidget


class WorkspaceSession(QObject):
    """Own workspace state, workspace UI composition, and teardown."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        render_catalog_manager: SceneRenderCatalogManager,
        intake: WorkspaceIntake | None = None,
        recent_videos: RecentVideos | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = get_logger(__name__)
        self._render_catalog_manager = render_catalog_manager
        self._intake = intake or default_workspace_intake()
        self._recent_videos = recent_videos or default_recent_videos()

        self._workspace_manager = WorkspaceManager(parent=self)
        self._content_browser = ContentBrowserWidget()
        self._center_area = SplitView()
        self._window = ApplicationWindow(
            content_browser=self._content_browser,
            center_area=self._center_area,
            parent=parent,
        )
        self._workspace_controller = WorkspaceController(
            workspace_manager=self._workspace_manager,
            content_browser=self._content_browser,
            center_area=self._center_area,
            render_catalog_manager=self._render_catalog_manager,
            parent=self._window,
        )
        self._welcome = self._center_area.welcome_widget()
        self._welcome.set_recent_videos(self._recent_videos.entries())
        self._welcome.recent_video_requested.connect(self.open_video)
        self._window.files_dropped.connect(self.open_files)

    def widget(self) -> ApplicationWindow:
        """Return the composed workspace widget tree."""
        return self._window

    def add_content(self, content: Content) -> None:
        """Add one content item to the workspace."""
        self._workspace_manager.add_content(content)

    def add_contents(self, contents: Sequence[Content]) -> None:
        """Add multiple content items to the workspace."""
        self._workspace_manager.add_contents(contents)

    def remove_content(self, content: Content) -> None:
        """Remove one content item from the workspace."""
        self._workspace_manager.remove_content(content)

    def clear(self) -> None:
        """Clear the workspace."""
        self._workspace_manager.clear()

    def load_startup_content(self, startup: StartupContent) -> bool:
        """Resolve startup content and add it to the workspace; return whether anything was added."""
        try:
            contents = startup.resolve(self._intake)
        except (TypeError, ValueError) as exc:
            self._logger.warning(str(exc))
            return False
        if not contents:
            self._logger.warning("Startup content did not resolve to any workspace content.")
            return False
        self._workspace_manager.add_contents(contents)
        self._logger.info(f"Added {len(contents)} startup content item(s)")
        return True

    def open_video(self, request: VideoFileStartup) -> None:
        """Open a video file request and remember it in the welcome screen's recent list."""
        if not self.load_startup_content(request):
            return
        self._recent_videos.record(request)
        self._welcome.set_recent_videos(self._recent_videos.entries())

    def open_files(self, paths: Sequence[Path]) -> None:
        """Open dropped files, asking for the overlay's data handler when more than one decoder may read it."""
        requests = video_file_requests(paths, self._intake)
        if not requests:
            self._logger.warning("No video files to open among the dropped files")
        for request in requests:
            if request.needs_handler:
                with AddVideoDialog(self._window, initial=request) as dialog:
                    dialog.exec()
                    confirmed = dialog.get_result()
                if confirmed is None:
                    continue
                request = confirmed
            self.open_video(request)

    def focused_offline_viewer(self) -> OfflineVideoViewerWidget | None:
        """Return the currently focused offline viewer, if any."""
        from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget

        widget = self._center_area.get_focused_widget()
        if isinstance(widget, OfflineVideoViewerWidget):
            return widget
        return None

    def focused_widget(self) -> WorkspaceWidget | None:
        """Return the currently focused workspace widget, regardless of type."""
        return self._center_area.get_focused_widget()

    def cleanup(self) -> None:
        """Release workspace viewers and related UI state."""
        self._workspace_controller.cleanup()
        self._logger.debug("Workspace session cleanup completed")

    def set_welcome_shortcut_manager(self, manager: ShortcutManager) -> None:
        """Configure the welcome screen to read shortcuts from *manager*."""
        self._center_area.set_welcome_shortcut_manager(manager)
