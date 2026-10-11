"""The Workspace view: everything the workspace holds, or the start panel while it is empty."""

from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.view_panel import ViewPanel
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.start_panel import StartPanel

WORKSPACE_VIEW_ID = "workspace"


class WorkspaceView(ViewPanel):
    """Show the start panel while the workspace is empty, and the content browser once it lists anything."""

    def __init__(
        self, content_browser: ContentBrowserWidget, start_panel: StartPanel, parent: QWidget | None = None
    ) -> None:
        super().__init__("Workspace", parent=parent)
        self._content_browser = content_browser
        self._start_panel = start_panel
        self._pages = QStackedWidget(self)
        self._pages.addWidget(start_panel)
        self._pages.addWidget(content_browser)
        self.add(self._pages)
        self.body.setStretchFactor(self._pages, 1)
        content_browser.rows_changed.connect(self._show_page)
        self._show_page()

    def sidebar_view(self) -> SidebarView:
        """Return this view as the sidebar's first view."""
        return SidebarView(WORKSPACE_VIEW_ID, "Workspace", Icon.WORKSPACE, ViewGroup.WORKSPACE, self)

    def showing_start_panel(self) -> bool:
        """Return whether the start panel is the page on screen."""
        return self._pages.currentWidget() is self._start_panel

    def _show_page(self) -> None:
        self._pages.setCurrentWidget(self._content_browser if self._content_browser.has_rows() else self._start_panel)
