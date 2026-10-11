"""A tree of browser sections: a caption per section, and the rows under it as the shared source row."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import cast

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.workspace.ui.browser_rows import BrowserSection, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.source_row import SourceRow, section_accessible_text, section_caption

ROW_ROLE = Qt.ItemDataRole.UserRole
"""The data role holding the ``WorkspaceBrowserRow`` of a row's tree item; section items hold none."""

_HIDDEN_ROLE = Qt.ItemDataRole.UserRole + 1
"""The data role holding, on a section's item, how many of its rows the filter leaves out."""

RowPredicate = Callable[[WorkspaceBrowserRow], bool]


class SectionTree(QTreeWidget):
    """Show browser sections, and report what the user does with their rows.

    A click on a section's caption folds or unfolds it, and the fold survives ``set_sections``. A folded section opens
    for a search with matches in it, and for a row of it that turns open, until that row closes or the user folds it
    again; neither changes the fold the user chose. A section with no row shown is hidden, unless it has no rows at all
    and its kind has an empty text to say instead, or the filter leaves out rows the search would show; the caption
    counts the rows the filter leaves out. Rows expand and collapse with Qt's branch arrows, and keep that state across
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
        self._folded: set[BrowserSection] = set()
        """The sections the user folded."""
        self._revealed: set[BrowserSection] = set()
        """The folded sections an open row keeps unfolded, until that row closes or the user folds the section."""
        self._opened: set[BrowserSection] = set()
        """The sections that held an open row at the last ``set_sections``."""
        self._folding = False
        """Whether the tree itself is folding or unfolding sections, so the change is not the user's."""
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
        # Section rows sit undecorated at the root, so the rows under them indent as top-level rows; a section's
        # caption draws its own arrow, and a click on it folds the section.
        self.setRootIsDecorated(False)
        # The row widgets draw their own hover and open edge.
        self.setStyleSheet("QTreeView::item:hover, QTreeView::item:selected { background: transparent; }")
        self.itemExpanded.connect(self._install_children)
        self.itemExpanded.connect(self._on_folded_or_unfolded)
        self.itemCollapsed.connect(self._on_folded_or_unfolded)
        self.itemClicked.connect(self._toggle_section)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.customContextMenuRequested.connect(self._on_context_menu)
        follow_appearance(self, self._rebuild)

    def set_sections(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        """Show *sections*, keeping which rows are expanded and which sections are folded."""
        opened = {section.kind for section in sections if _holds_open_row(section.rows)}
        self._revealed = (self._revealed & opened) | (opened - self._opened)
        self._opened = opened
        self._sections = sections
        self._rebuild()

    def set_filter(self, allowed: RowPredicate, matches: RowPredicate | None = None) -> None:
        """Show only the rows *allowed* passes, and with *matches*, only those that match, sit under a match, or hold
        one, which is revealed; a section with no row left is hidden.

        A section's caption counts the rows *allowed* leaves out that *matches* would show.
        """
        self._allowed = allowed
        self._matches = matches
        self._apply_filter()

    def has_rows(self) -> bool:
        """Return whether any section is shown."""
        return bool(self._sections)

    def _rebuild(self) -> None:
        """Build the items and the widgets of the rows that can be seen, for the current palette and text size."""
        expansion = {row.row_id: (item.isExpanded(), row.is_open) for item, row in self._row_items()}
        with self._folding_sections():
            self.clear()
            for section in self._sections:
                heading = QTreeWidgetItem()
                heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
                heading.addChildren([self._tree_item(row) for row in section.rows])
                self.addTopLevelItem(heading)
            for item, row in self._row_items():
                if row.is_open:
                    self._reveal(item)
            for item, row in self._row_items():
                if row.row_id in expansion:
                    was_expanded, was_open = expansion[row.row_id]
                    if was_open and not row.is_open:
                        # The viewer has moved on from this row, so it closes; the row it moved to was revealed above.
                        item.setExpanded(False)
                    elif was_open or not row.is_open:
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

    def _section_of(self, heading: QTreeWidgetItem) -> WorkspaceBrowserSection:
        """Return the section a section item heads."""
        return self._sections[self.indexOfTopLevelItem(heading)]

    def _show_caption(self, heading: QTreeWidgetItem) -> None:
        """Give *heading* the caption for its section, its fold, and the rows the filter leaves out."""
        section = self._section_of(heading)
        hidden = int(heading.data(0, _HIDDEN_ROLE) or 0)
        folded = not heading.isExpanded() if section.rows else None
        # The empty text starts where the rows a section would hold start, at their side margin.
        caption = section_caption(section, self.indentation() + Space.M, hidden=hidden, folded=folded)
        if (replaced := self.itemWidget(heading, 0)) is not None:
            # The tree deletes a replaced widget later and leaves it on screen until then.
            replaced.hide()
        self.setItemWidget(heading, 0, caption)
        heading.setData(0, Qt.ItemDataRole.AccessibleTextRole, section_accessible_text(section, hidden))

    @contextmanager
    def _folding_sections(self) -> Iterator[None]:
        """Mark the sections folded or unfolded inside the block as the tree's doing, not the user's."""
        was_folding = self._folding
        self._folding = True
        try:
            yield
        finally:
            self._folding = was_folding

    def _toggle_section(self, item: QTreeWidgetItem) -> None:
        """Fold or unfold a section whose caption was clicked, when it has rows to fold."""
        if item.parent() is None and self._section_of(item).rows:
            item.setExpanded(not item.isExpanded())

    def _on_folded_or_unfolded(self, item: QTreeWidgetItem) -> None:
        """Turn a section's arrow, and remember a fold the user made, by click or keyboard."""
        if item.parent() is not None:
            return
        kind = self._section_of(item).kind
        if not self._folding:
            if item.isExpanded():
                self._folded.discard(kind)
            else:
                self._folded.add(kind)
                self._revealed.discard(kind)
        self._show_caption(item)

    def _apply_filter(self) -> None:
        """Show or hide every row by the filter; show each section that has a row shown, has no rows and says so, or
        has rows the filter leaves out; and fold or unfold each section as the user, a search, and open rows ask."""
        with self._folding_sections():
            for index, section in enumerate(self._sections):
                heading = self.topLevelItem(index)
                if heading is None:
                    continue
                shown = False
                for child_index in range(heading.childCount()):
                    child = heading.child(child_index)
                    if child is not None:
                        shown = self._filter_item(child, ancestor_matches=False) or shown
                hidden = _count_shown(section.rows, lambda _row: True, self._matches) - _count_shown(
                    section.rows, self._allowed, self._matches
                )
                says_empty = not section.rows and bool(section.kind.empty_text)
                # A search matching only rows the filter leaves out keeps the section, so its count points to them.
                heading.setHidden(not (shown or hidden or says_empty))
                kind = section.kind
                searched = shown and self._matches is not None
                expanded = searched or kind not in self._folded or kind in self._revealed
                heading.setData(0, _HIDDEN_ROLE, hidden)
                heading.setExpanded(expanded)
                self._show_caption(heading)

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


def _holds_open_row(rows: Sequence[WorkspaceBrowserRow]) -> bool:
    """Return whether any of *rows*, or a row under them, is open in a viewer."""
    return any(row.is_open or _holds_open_row(row.children) for row in rows)


def _count_shown(
    rows: Sequence[WorkspaceBrowserRow],
    allowed: RowPredicate,
    matches: RowPredicate | None,
    *,
    ancestor_matches: bool = False,
) -> int:
    """Return how many of *rows* and the rows under them a filter of *allowed* and *matches* shows, as ``set_filter``
    does: a row is shown when *allowed* passes it and its parent is shown, and, with *matches*, when it matches, sits
    under a match, or holds one."""
    count = 0
    for row in rows:
        row_matches = matches is None or matches(row)
        below = _count_shown(row.children, allowed, matches, ancestor_matches=ancestor_matches or row_matches)
        if allowed(row) and (matches is None or ancestor_matches or row_matches or below > 0):
            count += 1 + below
    return count
