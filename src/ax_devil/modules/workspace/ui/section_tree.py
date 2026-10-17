"""A tree of browser sections: a caption per section, and the rows under it as the shared source row."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import cast

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QDialog, QFrame, QMenu, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.core.items import WorkspaceItem
from ax_devil.modules.workspace.ui.browser_rows import BrowserSection, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog
from ax_devil.modules.workspace.ui.source_row import SourceRow, row_look, section_accessible_text, section_caption

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
    ``set_sections`` by row id; a row that starts expanded, such as a playlist, expands when it first appears. A row
    that turns open is revealed. ``set_sections`` keeps the items and widgets of rows whose id and drawn values are
    unchanged, so a row's id must be stable; it builds them again only when the sections or the order of the rows
    they keep change. Row widgets are built for rows that can first be seen or whose look
    changed, so a collapsed row builds the widgets of its rows only once it is expanded; filtering reads row data, never
    widgets.

    ``row_activated`` carries a double-clicked row and whether Ctrl asked for it to the side, ``row_action_requested``
    a row whose action was pressed, and ``row_consideration_changed`` a consideration item and whether the user's
    Hide or Unhide asks for it to take part in playback. Right-clicking a row offers its menu, the same in every view,
    as far as the row has them, in groups split by separators: Open (Open, Open to the Side, the row's action),
    Inspect (Information, Export), Visibility (Hide or Unhide) and Edit (Rename, Remove); the other signals carry what
    the user picks there.
    """

    row_activated = Signal(object, bool)  # WorkspaceBrowserRow, to the side
    row_action_requested = Signal(object)  # WorkspaceBrowserRow
    row_consideration_changed = Signal(object, bool)  # ConsiderationItemRef, considered
    content_activated = Signal(object, int)  # Content, start index
    content_open_to_side_requested = Signal(object, int)  # Content, start index
    item_remove_requested = Signal(str)  # item id
    item_rename_requested = Signal(str, str)  # item id, new label; empty for the default name
    export_requested = Signal(object)  # OnScreenWorkspaceItem

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
        updatable = _updatable(self._sections, sections)
        self._sections = sections
        if updatable:
            self._update()
        else:
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
            self._restore_expansion(expansion)

    def _update(self) -> None:
        """Bring the items to the current sections, whose kinds and kept rows' order are those they show, and build
        widgets only for rows added or whose look changed."""
        expansion = {row.row_id: (item.isExpanded(), row.is_open) for item, row in self._row_items()}
        with self._folding_sections():
            for index, section in enumerate(self._sections):
                if (heading := self.topLevelItem(index)) is not None:
                    self._update_children(heading, section.rows)
            self._restore_expansion(expansion)

    def _update_children(self, parent: QTreeWidgetItem, rows: Sequence[WorkspaceBrowserRow]) -> None:
        """Make the items under *parent* show *rows*: drop the gone ones, add the new ones, update the kept ones."""
        ids = {row.row_id for row in rows}
        for index in reversed(range(parent.childCount())):
            if (child := parent.child(index)) is not None and _row_of(child).row_id not in ids:
                parent.removeChild(child)
        for position, row in enumerate(rows):
            child = parent.child(position)
            if child is None or _row_of(child).row_id != row.row_id:
                parent.insertChild(position, self._tree_item(row))
                continue
            child.setData(0, ROW_ROLE, row)
            child.setData(0, Qt.ItemDataRole.AccessibleTextRole, _accessible_text(row))
            if isinstance(widget := self.itemWidget(child, 0), SourceRow):
                if row_look(widget.row) == row_look(row):
                    widget.row = row
                else:
                    # The tree deletes a removed widget later and leaves it on screen until then.
                    widget.hide()
                    self.removeItemWidget(child, 0)
            self._update_children(child, row.children)

    def _restore_expansion(self, expansion: dict[str, tuple[bool, bool]]) -> None:
        """Reveal the open rows, restore the expansion the rows had by id, expand the new rows that start expanded,
        filter, and build the widgets of the rows under every expanded item."""
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
            elif row.expanded_at_first:
                item.setExpanded(True)
        self._apply_filter()
        iterator = QTreeWidgetItemIterator(self)
        while (item := iterator.value()) is not None:
            if item.isExpanded():
                self._install_children(item)
            iterator += 1

    def _tree_item(self, row: WorkspaceBrowserRow) -> QTreeWidgetItem:
        """Return an item for *row* and its children, without widgets."""
        item = QTreeWidgetItem()
        item.setData(0, ROW_ROLE, row)
        # The detail is the row widget's second line; screen readers get it from here.
        item.setData(0, Qt.ItemDataRole.AccessibleTextRole, _accessible_text(row))
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
        """Show the menu of the row under a right click, if it has anything to offer."""
        item = self.itemAt(position)
        if item is None or item.parent() is None:
            return
        menu = self._build_context_menu(_row_of(item))
        try:
            if menu.actions():
                menu.exec(self.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    def _build_context_menu(self, row: WorkspaceBrowserRow) -> QMenu:
        """Build the context menu for one row: its groups of actions in a fixed order, split by separators."""
        menu = QMenu(self)
        groups = (
            self._open_actions(row, menu),
            self._inspect_actions(row, menu),
            self._visibility_actions(row, menu),
            self._edit_actions(row, menu),
        )
        for group in (group for group in groups if group):
            if menu.actions():
                menu.addSeparator()
            menu.addActions(group)
        return menu

    def _open_actions(self, row: WorkspaceBrowserRow, menu: QMenu) -> list[QAction]:
        """Return the actions that open *row* in the preview pane or a new split, then the action the row offers."""
        actions: list[QAction] = []
        if row.activation_target is not None:
            content, start_index = row.activation_target
            open_action = QAction("Open", menu)
            open_action.setToolTip("Replace the preview (unpinned) pane")
            open_action.triggered.connect(lambda _checked=False: self.content_activated.emit(content, start_index))
            side_action = QAction("Open to the Side", menu)
            side_action.setToolTip("Open pinned in a new split (Ctrl+double-click)")
            side_action.triggered.connect(
                lambda _checked=False: self.content_open_to_side_requested.emit(content, start_index)
            )
            actions += [open_action, side_action]
        if (row_action := row.action) is not None:
            action = QAction(row_action.label or row_action.tooltip, menu)
            action.triggered.connect(lambda _checked=False: self.row_action_requested.emit(row))
            actions.append(action)
        return actions

    def _inspect_actions(self, row: WorkspaceBrowserRow, menu: QMenu) -> list[QAction]:
        """Return the actions that show or export what *row* is."""
        actions: list[QAction] = []
        if (information_factory := row.information_factory) is not None:
            info_action = QAction("Information", menu)
            info_action.triggered.connect(
                lambda _checked=False, factory=information_factory: self._show_item_information(factory())
            )
            actions.append(info_action)
        if (export_target := row.export_target) is not None:
            export_action = QAction("Export video" if row.is_open else "Export video (open it first)", menu)
            export_action.setEnabled(row.is_open)
            export_action.triggered.connect(lambda _checked=False: self.export_requested.emit(export_target))
            actions.append(export_action)
        return actions

    def _visibility_actions(self, row: WorkspaceBrowserRow, menu: QMenu) -> list[QAction]:
        """Return Hide or Unhide for a row that can be hidden from playback."""
        if (ref := row.consideration_ref) is None:
            return []
        action = QAction("Hide" if row.is_considered else "Unhide", menu)
        action.triggered.connect(lambda _checked=False: self.row_consideration_changed.emit(ref, not row.is_considered))
        return [action]

    def _edit_actions(self, row: WorkspaceBrowserRow, menu: QMenu) -> list[QAction]:
        """Return the actions that rename or remove the item *row* stands for."""
        if (workspace_item := row.item) is None:
            return []
        rename_action = QAction("Rename", menu)
        rename_action.setEnabled(workspace_item.renamable)
        rename_action.triggered.connect(
            lambda _checked=False, value=workspace_item, shown=row.label: self._rename_item(value, shown)
        )
        remove_action = QAction("Remove", menu)
        remove_action.triggered.connect(
            lambda _checked=False, item_id=workspace_item.id: self.item_remove_requested.emit(item_id)
        )
        return [rename_action, remove_action]

    def _rename_item(self, item: WorkspaceItem, shown_name: str) -> None:
        """Ask for a new name for *item* and request the rename when the user confirms it.

        *shown_name* is the label of the row the user clicked. An unlabeled item shows its default name there, which
        can differ from ``item.display_name`` when several playlists share the row. Keeping that name means no label.
        """
        with RenameDialog(item.label or shown_name, self) as dialog:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            name = dialog.name()
        if not item.label and name == shown_name:
            name = ""
        self.item_rename_requested.emit(item.id, name)

    def _show_item_information(self, info: WorkspaceItemInfo) -> None:
        """Display a simple modal summary for one workspace tree item."""
        with WorkspaceItemInfoDialog(info, self) as dialog:
            dialog.exec()


def _row_of(item: QTreeWidgetItem) -> WorkspaceBrowserRow:
    """Return the row a row item shows."""
    return cast(WorkspaceBrowserRow, item.data(0, ROW_ROLE))


def _accessible_text(row: WorkspaceBrowserRow) -> str:
    """Return what a screen reader says for *row*: its label, then its detail line."""
    return f"{row.label}, {row.detail}" if row.detail else row.label


def _updatable(old: Sequence[WorkspaceBrowserSection], new: Sequence[WorkspaceBrowserSection]) -> bool:
    """Return whether a tree showing *old* can become *new* by adding and removing rows alone: the same section kinds in
    the same order, and under every section and every kept row, unique ids with the ids of both in the same order."""
    return [section.kind for section in old] == [section.kind for section in new] and all(
        _rows_updatable(before.rows, after.rows) for before, after in zip(old, new, strict=True)
    )


def _rows_updatable(old: Sequence[WorkspaceBrowserRow], new: Sequence[WorkspaceBrowserRow]) -> bool:
    """Return whether *new* follows *old* as ``_updatable`` asks, here and under each row they share."""
    ids = [row.row_id for row in new]
    shown = set(ids)
    before = {row.row_id: row for row in old}
    if len(shown) != len(ids) or [row.row_id for row in old if row.row_id in shown] != [i for i in ids if i in before]:
        return False
    return all(_rows_updatable(before[row.row_id].children, row.children) for row in new if row.row_id in before)


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
