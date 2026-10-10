"""Content browser tree widget for workspace navigation.

Displays workspace contents in a tree view with support for double-click
activation (Ctrl+double-click opens to the side) and context menus.
"""

from __future__ import annotations

from collections.abc import Iterator

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.workspace.core.content import ConsiderationItemRef, Content
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.core.items import WorkspaceItem
from ax_devil.modules.workspace.ui.browser_rows import (
    WorkspaceBrowserIconKind,
    WorkspaceBrowserRow,
)
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog

TREE_INDENTATION_PX = Space.L
TREE_LABEL_COLUMN = 0
TREE_CONSIDERATION_COLUMN = 1
TREE_CONSIDERATION_COLUMN_WIDTH_PX = 24
TREE_ROW_ROLE = Qt.ItemDataRole.UserRole


_KIND_ICONS: dict[WorkspaceBrowserIconKind, Icon] = {
    "video": Icon.VIDEO,
    "live_video": Icon.LIVE_VIDEO,
    "playlist": Icon.PLAYLIST,
    "overlay": Icon.OVERLAY,
    "unavailable": Icon.WARNING,
    "pending": Icon.MORE,
}


class _ConsiderationToggle(QToolButton):
    """Small eye toggle used to include or exclude a tree item."""

    def __init__(self, *, considered: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setCheckable(True)
        self.setChecked(considered)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            """
            QToolButton {
                border: none;
                padding: 0px;
                margin: 0px;
                background: transparent;
            }
            QToolButton:hover {
                background: transparent;
            }
            QToolButton:checked {
                background: transparent;
            }
            """
        )
        self.toggled.connect(self._update_icon)
        self._update_icon(considered)

    def _update_icon(self, considered: bool) -> None:
        self.setIcon((Icon.SHOWN if considered else Icon.HIDDEN).icon())
        self.setToolTip(
            "Included in playback layout/navigation. Click to exclude."
            if considered
            else "Excluded from playback layout/navigation. Click to include."
        )


class ContentBrowserWidget(QWidget):
    """Tree view showing workspace contents with navigation and context menus."""

    content_activated = Signal(object, int)
    content_open_to_side_requested = Signal(object, int)
    item_consideration_change_requested = Signal(object, bool)
    item_remove_requested = Signal(str)  # item id
    item_rename_requested = Signal(str, str)  # item id, new label; empty for the default name
    export_requested = Signal(object)  # OnScreenWorkspaceItem
    rows_changed = Signal()  # After set_browser_rows; see has_rows

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilContentBrowser")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._show_excluded = False
        self._search_query = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(Space.S, Space.XS, Space.S, Space.XS)
        toolbar.setSpacing(Space.S)
        self._search_edit = self._create_search_edit()
        self._show_excluded_toggle = self._create_show_excluded_toggle()
        toolbar.addWidget(self._search_edit, 1)
        toolbar.addWidget(self._show_excluded_toggle)
        layout.addLayout(toolbar)

        self._tree = QTreeWidget()
        self._configure_tree()
        layout.addWidget(self._tree)

        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self.setStyleSheet("#AxDevilContentBrowser { background: palette(alternate-base); }")
        follow_appearance(self, self._apply_appearance)

    def set_browser_rows(self, rows: tuple[WorkspaceBrowserRow, ...]) -> None:
        """Render explicit workspace browser rows."""
        expansion = {
            self._row_data(item).row_id: (item.isExpanded(), self._row_data(item).is_open)
            for item in self._tree_items()
        }
        self._tree.clear()
        for row in rows:
            item = self._create_tree_item(row)
            self._tree.addTopLevelItem(item)
            self._install_tree_widgets(item)
            self._refresh_visual_state(item)
        self._apply_open_indicators()
        for item in self._tree_items():
            row = self._row_data(item)
            if row.row_id in expansion:
                was_expanded, was_open = expansion[row.row_id]
                if was_open or not row.is_open:
                    item.setExpanded(was_expanded)
        self._apply_visibility_filters()
        self.rows_changed.emit()

    def has_rows(self) -> bool:
        """Return whether the browser lists any workspace content."""
        return self._tree.topLevelItemCount() > 0

    def _tree_items(self) -> Iterator[QTreeWidgetItem]:
        """Iterate over all rows, including hidden and collapsed descendants."""
        iterator = QTreeWidgetItemIterator(self._tree)
        while (item := iterator.value()) is not None:
            yield item
            iterator += 1

    def _create_search_edit(self) -> QLineEdit:
        """Create the content-name search field."""
        search = QLineEdit(self)
        search.setPlaceholderText("Search items")
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self._on_search_text_changed)
        return search

    def _configure_tree(self) -> None:
        """Apply the shared visual and interaction settings for the content tree."""
        self._tree.setColumnCount(2)
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.setFrameShape(QFrame.Shape.NoFrame)
        self._tree.setIndentation(TREE_INDENTATION_PX)
        self._tree.setUniformRowHeights(True)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(TREE_LABEL_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(TREE_CONSIDERATION_COLUMN, QHeaderView.ResizeMode.Fixed)
        self._tree.setColumnWidth(TREE_CONSIDERATION_COLUMN, TREE_CONSIDERATION_COLUMN_WIDTH_PX)

    def _create_show_excluded_toggle(self) -> QToolButton:
        """Create the global toggle for showing or hiding excluded items."""
        toggle = QToolButton(self)
        toggle.setAutoRaise(True)
        toggle.setCheckable(True)
        toggle.setChecked(self._show_excluded)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setStyleSheet(
            """
            QToolButton {
                border: none;
                padding: 0px;
                margin: 0px;
                background: transparent;
            }
            QToolButton:hover {
                background: transparent;
            }
            """
        )
        self._update_show_excluded_icon(toggle, self._show_excluded)
        toggle.toggled.connect(self._on_show_excluded_toggled)
        return toggle

    def _update_show_excluded_icon(self, toggle: QToolButton, show: bool) -> None:
        """Update the icon and tooltip of the show-excluded browser filter."""
        if show:
            toggle.setIcon(Icon.FILTER_OFF.icon())
            toggle.setToolTip("Showing excluded items. Click to hide them from this list.")
        else:
            toggle.setIcon(Icon.FILTER.icon())
            toggle.setToolTip("Excluded items are hidden from this list. Click to show them.")

    def _on_show_excluded_toggled(self, show: bool) -> None:
        """Handle the global show-excluded toggle."""
        self._show_excluded = show
        self._update_show_excluded_icon(self._show_excluded_toggle, show)
        self._apply_visibility_filters()

    def _on_search_text_changed(self, text: str) -> None:
        """Filter workspace rows by their displayed names."""
        self._search_query = text.strip().casefold()
        self._apply_visibility_filters()

    def _apply_visibility_filters(self) -> None:
        """Show or hide items based on the search field and excluded-item toggle."""
        for i in range(self._tree.topLevelItemCount()):
            top_item = self._tree.topLevelItem(i)
            if top_item is not None:
                self._apply_item_visibility(top_item, ancestor_matches=False)

    def _apply_item_visibility(self, item: QTreeWidgetItem, *, ancestor_matches: bool) -> bool:
        """Show or hide a single item and return whether it is visible."""
        item_matches = self._item_matches_search(item)
        child_ancestor_matches = ancestor_matches or item_matches
        child_visible = False
        for i in range(item.childCount()):
            child = item.child(i)
            if child is not None:
                child_visible = (
                    self._apply_item_visibility(child, ancestor_matches=child_ancestor_matches) or child_visible
                )

        row = self._row_data(item)
        passes_consideration_filter = row.consideration_ref is None or row.is_considered or self._show_excluded
        search_active = bool(self._search_query)
        passes_search_filter = not search_active or ancestor_matches or item_matches or child_visible
        visible = passes_consideration_filter and passes_search_filter
        item.setHidden(not visible)

        if search_active and visible and child_visible:
            item.setExpanded(True)

        return visible

    def _item_matches_search(self, item: QTreeWidgetItem) -> bool:
        """Return whether the tree item label matches the active search query."""
        return not self._search_query or self._search_query in self._row_data(item).display_text.casefold()

    def _apply_appearance(self) -> None:
        """Recolor row text from the current palette."""
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item is not None:
                self._refresh_visual_state(item)

    def _create_tree_item(self, row: WorkspaceBrowserRow) -> QTreeWidgetItem:
        """Create a tree item for one explicit browser row."""
        item = QTreeWidgetItem([row.display_text, ""])
        item.setData(TREE_LABEL_COLUMN, TREE_ROW_ROLE, row)
        item.setIcon(TREE_LABEL_COLUMN, _KIND_ICONS[row.icon_kind].icon())
        for child_row in row.children:
            item.addChild(self._create_tree_item(child_row))
        return item

    def _row_data(self, item: QTreeWidgetItem) -> WorkspaceBrowserRow:
        """Return the stored row metadata for one tree item."""
        row = item.data(TREE_LABEL_COLUMN, TREE_ROW_ROLE)
        assert isinstance(row, WorkspaceBrowserRow)
        return row

    def _install_tree_widgets(self, item: QTreeWidgetItem) -> None:
        """Install item widgets after the tree item has been attached."""
        item_ref = self._row_data(item).consideration_ref
        if item_ref is not None:
            self._install_item_toggle(item, item_ref)

        for index in range(item.childCount()):
            child = item.child(index)
            if child is not None:
                self._install_tree_widgets(child)

    def _install_item_toggle(self, item: QTreeWidgetItem, item_ref: ConsiderationItemRef) -> None:
        """Attach the eye toggle for one consideration-capable tree item."""
        toggle = _ConsiderationToggle(
            considered=self._row_data(item).is_considered,
            parent=self._tree,
        )
        toggle.toggled.connect(
            lambda considered, ref=item_ref: self._request_item_consideration_change(ref, considered)
        )
        self._tree.setItemWidget(item, TREE_CONSIDERATION_COLUMN, toggle)

    def _request_item_consideration_change(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Request an item consideration change from the owning state layer."""
        self.item_consideration_change_requested.emit(item_ref, considered)

    def _refresh_visual_state(self, item: QTreeWidgetItem) -> None:
        """Derive and apply the visual state for item and descendants from local state."""
        row = self._row_data(item)

        palette = self.palette()
        color = palette.color(QPalette.ColorRole.Text)
        if not row.is_considered or row.unavailable_reason is not None:
            color.setAlphaF(0.5)
        brush = QBrush(color)
        item.setForeground(TREE_LABEL_COLUMN, brush)
        for column in range(1, self._tree.columnCount()):
            item.setForeground(column, brush)

        for index in range(item.childCount()):
            child = item.child(index)
            if child is not None:
                self._refresh_visual_state(child)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Open the double-clicked item; Ctrl+double-click opens it to the side."""
        if column == TREE_CONSIDERATION_COLUMN:
            return
        self._expand_item_path(item)
        row = self._row_data(item)
        if row.unavailable_reason is not None:
            QMessageBox.warning(self, "Open Content", f"{row.label}: {row.unavailable_reason}")
            return
        activation_target = row.activation_target
        if activation_target is None:
            return
        if QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            self.content_open_to_side_requested.emit(*activation_target)
        else:
            self.content_activated.emit(*activation_target)

    def _expand_item_path(self, item: QTreeWidgetItem) -> None:
        """Expand the clicked item and its ancestors so structure is visible immediately."""
        current: QTreeWidgetItem | None = item
        while current is not None:
            if current.childCount() > 0:
                current.setExpanded(True)
            current = current.parent()

    def _apply_open_indicators(self) -> None:
        """Apply open-item indicators to all visible tree rows."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item is not None:
                self._refresh_open_indicator(item)

    def _refresh_open_indicator(self, item: QTreeWidgetItem) -> None:
        """Style one row as open or closed, then recurse into children."""
        is_open = self._row_data(item).is_open
        for column in range(self._tree.columnCount()):
            font = item.font(column)
            font.setBold(is_open)
            item.setFont(column, font)

        item.setToolTip(TREE_LABEL_COLUMN, self._row_data(item).tooltip)

        if is_open:
            if item.childCount() > 0:
                item.setExpanded(True)
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()

        for i in range(item.childCount()):
            child = item.child(i)
            if child is not None:
                self._refresh_open_indicator(child)

    def _show_context_menu(self, position: QPoint) -> None:
        """Show a context menu for the item under the cursor."""
        item = self._tree.itemAt(position)
        if item is None:
            return

        menu = self._build_context_menu(item)
        try:
            if menu.actions():
                menu.exec(self._tree.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    def _build_context_menu(self, item: QTreeWidgetItem) -> QMenu:
        """Build the context menu for one tree item."""
        menu = QMenu(self)
        activation_target = self._row_data(item).activation_target
        if activation_target is not None:
            self._add_open_actions(menu, *activation_target)
        information_factory = self._row_data(item).information_factory
        if information_factory is not None:
            info_action = QAction("Information", menu)
            info_action.triggered.connect(
                lambda _checked=False, factory=information_factory: self._show_item_information(factory())
            )
            menu.addAction(info_action)

        row = self._row_data(item)
        if row.export_target is not None:
            export_action = QAction("Export video" if row.is_open else "Export video (open it first)", menu)
            export_action.setEnabled(row.is_open)
            export_action.triggered.connect(
                lambda _checked=False, target=row.export_target: self.export_requested.emit(target)
            )
            menu.addAction(export_action)

        workspace_item = row.item
        if workspace_item is not None:
            rename_action = QAction("Rename", menu)
            rename_action.setEnabled(workspace_item.renamable)
            rename_action.triggered.connect(
                lambda _checked=False, value=workspace_item, shown=row.label: self._rename_item(value, shown)
            )
            menu.addAction(rename_action)
            remove_action = QAction("Remove", menu)
            remove_action.triggered.connect(
                lambda _checked=False, item_id=workspace_item.id: self.item_remove_requested.emit(item_id)
            )
            menu.addAction(remove_action)

        return menu

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

    def _add_open_actions(self, menu: QMenu, content: Content, start_index: int) -> None:
        """Add actions that open the row in the preview pane or in a new split."""
        open_action = QAction("Open", menu)
        open_action.setToolTip("Replace the preview (unpinned) pane")
        open_action.triggered.connect(lambda _checked=False: self.content_activated.emit(content, start_index))
        side_action = QAction("Open to the Side", menu)
        side_action.setToolTip("Open pinned in a new split (Ctrl+double-click)")
        side_action.triggered.connect(
            lambda _checked=False: self.content_open_to_side_requested.emit(content, start_index)
        )
        menu.addAction(open_action)
        menu.addAction(side_action)
        menu.addSeparator()

    def _show_item_information(self, info: WorkspaceItemInfo) -> None:
        """Display a simple modal summary for one workspace tree item."""
        with WorkspaceItemInfoDialog(info, self) as dialog:
            dialog.exec()
