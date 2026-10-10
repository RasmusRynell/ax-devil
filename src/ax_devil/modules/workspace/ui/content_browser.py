"""Content browser tree widget for workspace navigation.

Displays workspace contents in a tree view with support for double-click
activation (Ctrl+double-click opens to the side) and context menus.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from html import escape
from typing import cast

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFontMetrics, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole
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


_OPEN_EDGE_PX = 2
_SECTION_MARK_PX = 8


@dataclass(frozen=True)
class _KindMark:
    """The icon a row kind shows, in its color on the dark and on the light theme."""

    icon: Icon
    dark: str
    light: str

    def color(self, palette: QPalette) -> QColor:
        """Return the mark's color for the theme *palette* belongs to."""
        return QColor(self.dark if palette.color(QPalette.ColorRole.Text).lightnessF() > 0.5 else self.light)


# Each kind keeps one icon and color, so live streams, videos, and playlists read apart in a mixed workspace.
_KIND_MARKS: dict[WorkspaceBrowserIconKind, _KindMark] = {
    "live_video": _KindMark(Icon.LIVE_VIDEO, "#e5534b", "#c4362e"),
    "video": _KindMark(Icon.VIDEO, "#a8b3bf", "#526477"),
    "playlist": _KindMark(Icon.PLAYLIST, "#b18cf0", "#6f45c2"),
    "overlay": _KindMark(Icon.OVERLAY, "#8f9aa6", "#607080"),
    "unavailable": _KindMark(Icon.WARNING, *StatusColor.WARNING.value),
    "pending": _KindMark(Icon.MORE, "#858585", "#5a6a7d"),
}


def _row_at(index: QModelIndex | QPersistentModelIndex) -> WorkspaceBrowserRow:
    """Return the row stored on the label cell of *index*'s row."""
    return cast(WorkspaceBrowserRow, index.sibling(index.row(), TREE_LABEL_COLUMN).data(TREE_ROW_ROLE))


class _RowDelegate(QStyledItemDelegate):
    """Draw a row's label with its muted detail beside it, and section rows as captions with a count."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:  # noqa: N802
        """Give every row, section rows included, the shared row height; the tree takes it from its first row."""
        return QSize(super().sizeHint(option, index).width(), Height.ROW.px)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Draw the row."""
        row = _row_at(index)
        if row.is_section:
            self._paint_section(painter, option, row)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style()
        detail = row.detail
        # The style reports a text rect only as wide as the text; the label and detail share the cell after the icon.
        text_left = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget).left()
        text_rect = QRect(text_left, opt.rect.top(), opt.rect.right() - text_left - Space.XS, opt.rect.height())
        detail_metrics = QFontMetrics(TextRole.SMALL.font())
        # The detail takes the room the label leaves, and at least half the row when both do not fit.
        label_width = opt.fontMetrics.horizontalAdvance(opt.text)
        room = max(text_rect.width() - label_width - Space.M, text_rect.width() // 2)
        detail_advance = detail_metrics.horizontalAdvance(detail)
        detail_width = min(detail_advance, room) if detail else 0
        if detail:
            label_room = max(0, text_rect.width() - detail_width - Space.M)
            # Qt also elides text exactly as wide as the room, so elide only text that does not fit.
            if label_width > label_room:
                opt.text = opt.fontMetrics.elidedText(opt.text, Qt.TextElideMode.ElideMiddle, label_room)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        if not detail:
            return
        painter.save()
        color = opt.palette.color(QPalette.ColorRole.PlaceholderText)
        if not row.is_considered or row.unavailable_reason is not None:
            color.setAlphaF(0.6)
        painter.setPen(color)
        painter.setFont(TextRole.SMALL.font())
        detail_rect = QRect(text_rect.right() - detail_width + 1, text_rect.top(), detail_width, text_rect.height())
        painter.drawText(
            detail_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            detail
            if detail_advance <= detail_width
            else detail_metrics.elidedText(detail, Qt.TextElideMode.ElideMiddle, detail_width),
        )
        painter.restore()

    def _paint_section(self, painter: QPainter, option: QStyleOptionViewItem, row: WorkspaceBrowserRow) -> None:
        """Draw a section caption: its kind's color mark, the title, the count, and a rule to the right edge."""
        rect = option.rect.adjusted(Space.S, 0, -Space.S, 0)
        palette = option.palette
        center_y = rect.center().y()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mark = QRectF(rect.left(), center_y - _SECTION_MARK_PX / 2 + 1, _SECTION_MARK_PX, _SECTION_MARK_PX)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_KIND_MARKS[row.icon_kind].color(palette))
        painter.drawRoundedRect(mark, Radius.CONTROL / 2, Radius.CONTROL / 2)
        font = TextRole.CAPTION.font()
        metrics = QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        text = f"{row.label}  {row.summary}"
        text_left = rect.left() + _SECTION_MARK_PX + Space.S
        painter.drawText(
            QRect(text_left, rect.top(), rect.width(), rect.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        rule_left = text_left + metrics.horizontalAdvance(text) + Space.S
        if rule_left < rect.right():
            rule = QColor(palette.color(QPalette.ColorRole.Text))
            rule.setAlphaF(0.12)
            painter.setPen(rule)
            painter.drawLine(rule_left, center_y + 1, rect.right(), center_y + 1)
        painter.restore()


class _BrowserTree(QTreeWidget):
    """The content tree; a row open in a viewer gets an accent edge along the left side."""

    def drawRow(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Draw the row, then its open edge."""
        super().drawRow(painter, option, index)
        if _row_at(index).is_open:
            rect = option.rect
            edge = QRect(0, rect.top() + Space.XS, _OPEN_EDGE_PX, rect.height() - 2 * Space.XS)
            # The application palette has the accent; this tree's stylesheet-resolved palette does not.
            painter.fillRect(edge, QGuiApplication.palette().color(QPalette.ColorRole.Highlight))


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

        self._tree = _BrowserTree()
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
        self._reveal_open_rows_and_describe()
        for item in self._tree_items():
            row = self._row_data(item)
            if row.is_section:
                item.setFirstColumnSpanned(True)
                item.setExpanded(True)
            elif row.row_id in expansion:
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
        # Section rows sit undecorated at the root, so the rows under them indent as top-level rows used to. They
        # cannot be collapsed: nothing would show that one was.
        self._tree.setRootIsDecorated(False)
        self._tree.itemCollapsed.connect(self._keep_section_expanded)
        self._tree.setItemDelegateForColumn(TREE_LABEL_COLUMN, _RowDelegate(self._tree))
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(TREE_LABEL_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(TREE_CONSIDERATION_COLUMN, QHeaderView.ResizeMode.Fixed)
        self._tree.setColumnWidth(TREE_CONSIDERATION_COLUMN, TREE_CONSIDERATION_COLUMN_WIDTH_PX)

    def _keep_section_expanded(self, item: QTreeWidgetItem) -> None:
        """Expand a section row again when the keyboard collapses it."""
        if self._row_data(item).is_section:
            item.setExpanded(True)

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
        return not self._search_query or self._search_query in self._row_data(item).search_text.casefold()

    def _apply_appearance(self) -> None:
        """Recolor row text from the current palette."""
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item is not None:
                self._refresh_visual_state(item)

    def _create_tree_item(self, row: WorkspaceBrowserRow) -> QTreeWidgetItem:
        """Create a tree item for one explicit browser row."""
        item = QTreeWidgetItem([row.label, ""])
        item.setData(TREE_LABEL_COLUMN, TREE_ROW_ROLE, row)
        # The detail is painted beside the label; screen readers get it from here.
        detail = row.summary if row.is_section else row.detail
        item.setData(
            TREE_LABEL_COLUMN, Qt.ItemDataRole.AccessibleTextRole, f"{row.label}, {detail}" if detail else row.label
        )
        if row.is_section:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
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
        if not row.is_section:
            mark = _KIND_MARKS[row.icon_kind]
            item.setIcon(TREE_LABEL_COLUMN, mark.icon.icon(mark.color(palette)))
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

    def _reveal_open_rows_and_describe(self) -> None:
        """Apply open-item indicators to all visible tree rows."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item is not None:
                self._reveal_open_and_describe(item)

    def _reveal_open_and_describe(self, item: QTreeWidgetItem) -> None:
        """Expand the path to an open row and give each row its hover text, then recurse into children."""
        row = self._row_data(item)
        is_open = row.is_open
        if not row.is_section:
            lines = "".join(f"<br>{escape(line)}" for line in row.tooltip.splitlines())
            item.setToolTip(TREE_LABEL_COLUMN, f"<b>{escape(row.label)}</b>{lines}")

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
                self._reveal_open_and_describe(child)

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
