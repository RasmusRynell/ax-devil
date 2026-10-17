"""The frame every workspace view shares: a header with a summary, then a search and a Filter over one section tree."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMenu, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import muted_label
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.chrome.view_panel import ViewPanel
from ax_devil.modules.workspace.ui.browser_rows import Facet, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.section_tree import SectionTree


class SectionsView(ViewPanel):
    """A sidebar view of the workspace titled *title*: a summary at the right of its header, a search field and a
    Filter menu, then ``tree``; a subclass adds its own tools beside the search and its own widgets below the tree.

    The view decides only which rows its search and Filter show: its controller gives it sections and a summary, and
    hands ``tree`` to ``WorkspaceController.connect_tree``. The search matches each row's search text. The Filter
    offers the facets of the rows listed, grouped, in each group that has a choice to make, and hides the rows with a
    facet the user unchecked. With an *empty_page*, the view shows that page alone while it lists no section.
    """

    def __init__(
        self,
        view_id: str,
        title: str,
        icon: Icon,
        *,
        group: ViewGroup = ViewGroup.SOURCES,
        search_placeholder: str = "Search",
        editable_title: bool = False,
        empty_page: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        self._summary = muted_label("")
        self._summary.hide()
        super().__init__(title, header_widget=self._summary, parent=parent, editable_title=editable_title)
        self._sidebar = (view_id, title, icon, group)
        self._query = ""
        self._toggled: set[Facet] = set()
        """The facets the user showed or hid, against how they start."""
        self._offered: tuple[Facet, ...] = ()

        self._search = QLineEdit(self)
        self._search.setPlaceholderText(search_placeholder)
        self._search.setClearButtonEnabled(True)
        self._search.addAction(Icon.SEARCH.icon(), QLineEdit.ActionPosition.LeadingPosition)
        self._search.textChanged.connect(self._on_search)
        self._filter_menu = QMenu(self)
        self._filter = MenuButton("Filter", self._filter_menu)
        self._filter.setToolTip("Show or hide rows by what they are")
        self._filter.setEnabled(False)
        toolbar = QWidget(self)
        self._tools = QHBoxLayout(toolbar)
        self._tools.setContentsMargins(0, 0, 0, 0)
        self._tools.setSpacing(Space.S)
        self._tools.addWidget(self._search, 1)
        self._tools.addWidget(self._filter)
        self._toolbar = self.add_padded(toolbar, top=Space.S, bottom=Space.XS)

        self.tree = SectionTree(self)
        self.add(self.tree, 1)
        self._empty_page = empty_page
        if empty_page is not None:
            self.add(empty_page, 1)
        self._apply_filter()
        self._show_page()

    def sidebar_view(self) -> SidebarView:
        """Return this view as a sidebar view."""
        view_id, title, icon, group = self._sidebar
        return SidebarView(view_id, title, icon, group, self)

    def add_tool(self, widget: QWidget) -> None:
        """Put *widget* in the toolbar, after the search and the tools added before it."""
        self._tools.insertWidget(self._tools.count() - 1, widget)

    def set_sections(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        """List *sections*, and offer the facets of their rows in the Filter."""
        self.tree.set_sections(sections)
        self._offer(sorted({facet for row in _rows(sections) for facet in row.facets}, key=_facet_order))
        self._show_page()

    def set_summary(self, text: str, tooltip: str = "") -> None:
        """Show *text* at the right of the header, with *tooltip* on hover, or nothing there when it is empty."""
        self._summary.setText(text)
        self._summary.setToolTip(tooltip)
        self._summary.setVisible(bool(text))

    def _offer(self, facets: Sequence[Facet]) -> None:
        """Offer *facets* in the Filter, in groups, leaving out a group with one value unless the user hid it."""
        values: dict[str, list[Facet]] = {}
        for facet in facets:
            values.setdefault(facet.group, []).append(facet)
        offered = tuple(
            facet
            for group in values.values()
            if len(group) > 1 or any(facet in self._toggled for facet in group)
            for facet in group
        )
        if offered == self._offered:
            return
        self._offered = offered
        self._filter_menu.clear()
        group_name: str | None = None
        for facet in offered:
            if facet.group != group_name:
                group_name = facet.group
                self._filter_menu.addSection(group_name)
            action = self._filter_menu.addAction(facet.value)
            action.setCheckable(True)
            action.setChecked(not self._is_hidden(facet))
            action.toggled.connect(lambda _shown, facet=facet: self._toggle(facet))
        self._filter.setEnabled(bool(offered))

    def _is_hidden(self, facet: Facet) -> bool:
        return facet in self._toggled

    def _toggle(self, facet: Facet) -> None:
        self._toggled ^= {facet}
        self._apply_filter()

    def _on_search(self, text: str) -> None:
        self._query = text.strip().casefold()
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._query
        self.tree.set_filter(
            lambda row: not any(self._is_hidden(facet) for facet in row.facets),
            (lambda row: query in row.search_text.casefold()) if query else None,
        )

    def _show_page(self) -> None:
        """Show the empty page alone while no section is listed, if there is one."""
        if self._empty_page is None:
            return
        empty = not self.tree.has_rows()
        self._empty_page.setVisible(empty)
        self._toolbar.setVisible(not empty)
        self.tree.setVisible(not empty)


def _rows(sections: Sequence[WorkspaceBrowserSection]) -> Iterator[WorkspaceBrowserRow]:
    """Iterate over every row of *sections*, the rows under rows included."""
    stack = [row for section in sections for row in section.rows]
    while stack:
        row = stack.pop()
        yield row
        stack.extend(row.children)


def _facet_order(facet: Facet) -> tuple[str, str]:
    return facet.group, facet.value
