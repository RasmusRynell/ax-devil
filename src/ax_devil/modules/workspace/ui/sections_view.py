"""The frame the source views share: a header with a summary over one section tree."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import muted_label
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.view_panel import ViewPanel
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.section_tree import SectionTree


class SectionsView(ViewPanel):
    """A sources view titled *title*, with a short summary at the right of its header, over ``tree``.

    The view decides nothing: its controller gives it sections and a summary, and hands ``tree`` to
    ``WorkspaceController.connect_tree``. A subclass adds what is its own below the tree.
    """

    def __init__(self, view_id: str, title: str, icon: Icon, parent: QWidget | None = None) -> None:
        self._summary = muted_label("")
        self._summary.hide()
        super().__init__(title, header_widget=self._summary, parent=parent)
        self._view_id = view_id
        self._icon = icon
        self.tree = SectionTree(self)
        self.add(self.tree, 1)

    def sidebar_view(self) -> SidebarView:
        """Return this view as a sidebar view among the sources."""
        return SidebarView(self._view_id, self.title(), self._icon, ViewGroup.SOURCES, self)

    def set_sections(self, sections: tuple[WorkspaceBrowserSection, ...], summary: str = "") -> None:
        """List *sections*, and show *summary* in the header, or nothing there when it is empty."""
        self.tree.set_sections(sections)
        self._summary.setText(summary)
        self._summary.setVisible(bool(summary))
