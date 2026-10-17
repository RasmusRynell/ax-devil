"""The frame every workspace view shares: a header with a summary, a toolbar with Search, Sort and Filter, one section
tree, a row of footer buttons, and an optional footer panel."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMenu, QVBoxLayout, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import muted_label
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.chrome.view_panel import ViewPanel
from ax_devil.modules.workspace.ui.browser_rows import (
    WORKSPACE_ORDER,
    Facet,
    RowOrder,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.section_tree import SectionTree


class SectionsView(ViewPanel):
    """A sidebar view of the workspace titled *title*, laid out the same for every view: a summary at the right of its
    header, a toolbar with Search, Sort and Filter, ``tree``, a row of footer buttons directly under it, and a footer
    panel below that.

    The view decides only which rows its search, Sort and Filter show: its controller gives it sections, a summary,
    the orders, the footer buttons and the footer panel, and hands ``tree`` to ``WorkspaceController.connect_tree``.
    The search matches each row's search text. Sort appears with two or more orders and arranges the top-level rows of
    each section; the chosen order is kept while the sections change. The Filter appears when the rows listed have
    facets to choose from: it offers them, grouped, in each group that has a choice to make, and hides the rows with
    a facet the user unchecked. With an *empty_page*, the view shows that page alone while it lists no section.
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
        self._sections: tuple[WorkspaceBrowserSection, ...] = ()
        self._orders: tuple[RowOrder, ...] = (WORKSPACE_ORDER,)
        self._order = WORKSPACE_ORDER

        self._search = QLineEdit(self)
        self._search.setPlaceholderText(search_placeholder)
        self._search.setClearButtonEnabled(True)
        self._search.addAction(Icon.SEARCH.icon(), QLineEdit.ActionPosition.LeadingPosition)
        self._search.textChanged.connect(self._on_search)
        self._filter_menu = QMenu(self)
        self._filter = MenuButton("Filter", self._filter_menu)
        self._filter.setToolTip("Show or hide rows by what they are")
        self._filter.hide()
        self._order_menu = QMenu(self)
        self._sort = MenuButton("Sort", self._order_menu)
        self._sort.setToolTip("The order of the rows")
        self._sort.hide()
        toolbar = QWidget(self)
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(Space.S)
        tools.addWidget(self._search, 1)
        tools.addWidget(self._sort)
        tools.addWidget(self._filter)
        self._toolbar = self.add_padded(toolbar, top=Space.S, bottom=Space.XS)

        self.tree = SectionTree(self)
        self.add(self.tree, 1)
        self._empty_page = empty_page
        if empty_page is not None:
            self.add(empty_page, 1)
        self._empty = False
        buttons = QWidget(self)
        self._footer_buttons = QHBoxLayout(buttons)
        self._footer_buttons.setContentsMargins(0, 0, 0, 0)
        self._footer_buttons.setSpacing(Space.S)
        self._footer_buttons.addStretch(1)
        self._footer_button_row = self.add_padded(buttons, top=Space.S, bottom=Space.S)
        self._footer_button_row.hide()
        self._footer_panel = QWidget(self)
        self._footer_panel_layout = QVBoxLayout(self._footer_panel)
        self._footer_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.add(self._footer_panel)
        self._footer_panel.hide()
        self._apply_filter()
        self._show_page()

    def sidebar_view(self) -> SidebarView:
        """Return this view as a sidebar view."""
        view_id, title, icon, group = self._sidebar
        return SidebarView(view_id, title, icon, group, self)

    def set_orders(self, orders: Sequence[RowOrder]) -> None:
        """Offer *orders* in the Sort, which shows only with two or more; the order chosen before stays chosen if
        *orders* has one with its label, and else the first applies."""
        self._orders = tuple(orders)
        self._order = next((order for order in orders if order.label == self._order.label), self._orders[0])
        self._order_menu.clear()
        group = QActionGroup(self._order_menu)
        for order in orders:
            action = self._order_menu.addAction(order.label)
            action.setCheckable(True)
            action.setChecked(order is self._order)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, order=order: self._choose_order(order))
        self._sort.setVisible(len(orders) > 1)
        self._list()

    def add_footer_button(self, text: str, icon: Icon) -> SmallButton:
        """Add a button to the row directly under the tree, after the ones added before it, and return it."""
        button = SmallButton(text, icon)
        self._footer_buttons.insertWidget(self._footer_buttons.count() - 1, button)
        self._show_footer()
        return button

    def set_footer_panel(self, widget: QWidget) -> None:
        """Show *widget* below the footer buttons."""
        self._footer_panel_layout.addWidget(widget)
        self._show_footer()

    def set_sections(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        """List *sections* in the chosen order, and offer the facets of their rows in the Filter."""
        self._sections = sections
        self._list()
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
        self._filter.setVisible(bool(offered))

    def _choose_order(self, order: RowOrder) -> None:
        self._order = order
        self._list()

    def _list(self) -> None:
        """List the stored sections in the chosen order."""
        self.tree.set_sections(self._order.arrange(self._sections))

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
        self._empty = not self.tree.has_rows()
        self._empty_page.setVisible(self._empty)
        self._toolbar.setVisible(not self._empty)
        self.tree.setVisible(not self._empty)
        self._show_footer()

    def _show_footer(self) -> None:
        """Show the footer buttons and the footer panel, each only if it holds something and the empty page is not
        showing."""
        self._footer_button_row.setVisible(self._footer_buttons.count() > 1 and not self._empty)
        self._footer_panel.setVisible(self._footer_panel_layout.count() > 0 and not self._empty)


def _rows(sections: Sequence[WorkspaceBrowserSection]) -> Iterator[WorkspaceBrowserRow]:
    """Iterate over every row of *sections*, the rows under rows included."""
    stack = [row for section in sections for row in section.rows]
    while stack:
        row = stack.pop()
        yield row
        stack.extend(row.children)


def _facet_order(facet: Facet) -> tuple[str, str]:
    return facet.group, facet.value
