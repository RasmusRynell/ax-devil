"""A tree of browser sections: a caption per section, and the rows under it as the shared source row."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.source_row import SourceRow, section_caption

ROW_ROLE = Qt.ItemDataRole.UserRole
"""The data role holding the ``WorkspaceBrowserRow`` of a row's tree item; section items hold none."""

RowPredicate = Callable[[WorkspaceBrowserRow], bool]


class SectionTree(QTreeWidget):
    """Show browser sections, and report what the user does with their rows.

    Sections always stay expanded; rows expand and collapse with Qt's branch arrows, and keep that state across
    ``set_sections`` by row id. A row that turns open is revealed. Row widgets are built when their row can first be
    seen, so a long playlist builds its entries' widgets only once it is expanded; filtering reads row data, never
    widgets.

    ``row_activated`` carries a double-clicked row and whether Ctrl asked for it to the side, ``row_action_requested``
    a row whose action was pressed, ``row_consideration_changed`` a consideration item and the state its eye asks for,
    and ``context_menu_requested`` the row under a right click and where to show a menu.
    """

    row_activated = Signal(object, bool)  # WorkspaceBrowserRow, to the side
    row_action_requested = Signal(object)  # WorkspaceBrowserRow
    row_consideration_changed = Signal(object, bool)  # ConsiderationItemRef, considered
    context_menu_requested = Signal(object, QPoint)  # WorkspaceBrowserRow, global position

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sections: tuple[WorkspaceBrowserSection, ...] = ()
        self._allowed: RowPredicate = lambda _row: True
        self._matches: RowPredicate | None = None
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.setExpandsOnDoubleClick(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setIndentation(Space.L)
        # Rows with a detail are two lines high.
        self.setUniformRowHeights(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Section rows sit undecorated at the root, so the rows under them indent as top-level rows. They cannot be
        # collapsed: nothing would show that one was.
        self.setRootIsDecorated(False)
        # The row widgets draw their own hover and open edge.
        self.setStyleSheet("QTreeView::item:hover, QTreeView::item:selected { background: transparent; }")
        self.itemExpanded.connect(self._install_children)
        self.itemCollapsed.connect(self._keep_section_expanded)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.customContextMenuRequested.connect(self._on_context_menu)
        follow_appearance(self, self._rebuild)

    def set_sections(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        """Show *sections*, keeping which rows are expanded."""
        self._sections = sections
        self._rebuild()

    def set_filter(self, allowed: RowPredicate, matches: RowPredicate | None = None) -> None:
        """Show only the rows *allowed* passes, and with *matches*, only those that match, sit under a match, or hold
        one, which is revealed; a section with no row left is hidden."""
        self._allowed = allowed
        self._matches = matches
        self._apply_filter()

    def has_rows(self) -> bool:
        """Return whether any section is shown."""
        return bool(self._sections)

    def _rebuild(self) -> None:
        """Build the items and the widgets of the rows that can be seen, for the current palette and text size."""
        expansion = {row.row_id: (item.isExpanded(), row.is_open) for item, row in self._row_items()}
        self.clear()
        for section in self._sections:
            heading = QTreeWidgetItem()
            heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
            heading.setData(0, Qt.ItemDataRole.AccessibleTextRole, f"{section.kind.title}, {len(section.rows)}")
            heading.addChildren([self._tree_item(row) for row in section.rows])
            self.addTopLevelItem(heading)
            self.setItemWidget(heading, 0, section_caption(section))
            heading.setExpanded(True)
            self._install_children(heading)
        for item, row in self._row_items():
            if row.is_open:
                self._reveal(item)
        for item, row in self._row_items():
            if row.row_id in expansion:
                was_expanded, was_open = expansion[row.row_id]
                if was_open or not row.is_open:
                    item.setExpanded(was_expanded)
        self._apply_filter()

    def _tree_item(self, row: WorkspaceBrowserRow) -> QTreeWidgetItem:
        """Return an item for *row* and its children, without widgets."""
        item = QTreeWidgetItem()
        item.setData(0, ROW_ROLE, row)
        # The detail is the row widget's second line; screen readers get it from here.
        item.setData(0, Qt.ItemDataRole.AccessibleTextRole, f"{row.label}, {row.detail}" if row.detail else row.label)
        item.addChildren([self._tree_item(child) for child in row.children])
        return item

    def _install_children(self, item: QTreeWidgetItem) -> None:
        """Give the children of *item*, now expanded, the row widgets they do not have yet."""
        for index in range(item.childCount()):
            child = item.child(index)
            if child is None or self.itemWidget(child, 0) is not None:
                continue
            widget = SourceRow(_row_of(child))
            widget.action_requested.connect(self.row_action_requested)
            widget.consideration_changed.connect(self.row_consideration_changed)
            self.setItemWidget(child, 0, widget)

    def _row_items(self) -> Iterator[tuple[QTreeWidgetItem, WorkspaceBrowserRow]]:
        """Iterate over every row item with its row, hidden and collapsed ones included, depth first."""
        iterator = QTreeWidgetItemIterator(self)
        while (item := iterator.value()) is not None:
            if (row := item.data(0, ROW_ROLE)) is not None:
                yield item, cast(WorkspaceBrowserRow, row)
            iterator += 1

    def _reveal(self, item: QTreeWidgetItem) -> None:
        """Expand *item*, when it has rows under it, and every row above it."""
        if item.childCount() > 0:
            item.setExpanded(True)
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()

    def _keep_section_expanded(self, item: QTreeWidgetItem) -> None:
        """Expand a section row again when the keyboard collapses it."""
        if item.parent() is None:
            item.setExpanded(True)

    def _apply_filter(self) -> None:
        """Show or hide every row by the filter, and each section by whether any of its rows is shown."""
        for index in range(self.topLevelItemCount()):
            heading = self.topLevelItem(index)
            if heading is None:
                continue
            shown = False
            for child_index in range(heading.childCount()):
                child = heading.child(child_index)
                if child is not None:
                    shown = self._filter_item(child, ancestor_matches=False) or shown
            heading.setHidden(not shown)

    def _filter_item(self, item: QTreeWidgetItem, *, ancestor_matches: bool) -> bool:
        """Show or hide *item* and its children, and return whether *item* is shown."""
        row = _row_of(item)
        matches = self._matches is None or self._matches(row)
        child_shown = False
        for index in range(item.childCount()):
            child = item.child(index)
            if child is not None:
                child_shown = self._filter_item(child, ancestor_matches=ancestor_matches or matches) or child_shown
        searching = self._matches is not None
        shown = self._allowed(row) and (not searching or ancestor_matches or matches or child_shown)
        item.setHidden(not shown)
        if searching and shown and child_shown:
            item.setExpanded(True)
        return shown

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Report a double-clicked row, to the side with Ctrl, and show the rows under it and above it."""
        if item.parent() is None:
            return
        self._reveal(item)
        to_side = bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        self.row_activated.emit(_row_of(item), to_side)

    def _on_context_menu(self, position: QPoint) -> None:
        """Report the row under a right click, with the screen position to show a menu at."""
        item = self.itemAt(position)
        if item is not None and item.parent() is not None:
            self.context_menu_requested.emit(_row_of(item), self.viewport().mapToGlobal(position))


def _row_of(item: QTreeWidgetItem) -> WorkspaceBrowserRow:
    """Return the row a row item shows."""
    return cast(WorkspaceBrowserRow, item.data(0, ROW_ROLE))
