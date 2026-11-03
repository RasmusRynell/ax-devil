"""The sidebar: one view at a time, chosen from the activity bar."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QStackedWidget, QWidget

from ax_devil.modules.chrome.sidebar_view import SidebarView


class SidebarPanel(QStackedWidget):
    """Hosts the sidebar views and shows the chosen one; each view draws its own header and content."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilSidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._views: dict[str, SidebarView] = {}

    def add_view(self, view: SidebarView) -> None:
        """Host *view*; the first one added is shown. A second view with the same id is a programming error."""
        if view.view_id in self._views:
            raise ValueError(f"Duplicate sidebar view id: {view.view_id}")
        self._views[view.view_id] = view
        self.addWidget(view.widget)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the minimum of the view on screen, so a narrow view is not held to the widest view's minimum."""
        current = self.currentWidget()
        hint = current.minimumSizeHint() if current is not None else QSize(0, 0)
        return hint.grownBy(self.contentsMargins())

    def show_view(self, view_id: str) -> None:
        """Show the view with *view_id*."""
        self.setCurrentWidget(self._views[view_id].widget)

    def current_view_id(self) -> str | None:
        """Return the id of the view on screen, or None before any view was added."""
        current = self.currentWidget()
        return next((view.view_id for view in self._views.values() if view.widget is current), None)
