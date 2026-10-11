"""Content browser tree widget for workspace navigation.

Displays workspace contents in a tree view with support for double-click
activation (Ctrl+double-click opens to the side) and context menus.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from html import escape
from typing import cast

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFontMetrics, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
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
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.section_caption import paint_section_caption
from ax_devil.modules.chrome.theme import KindColor, StatusColor
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole
from ax_devil.modules.workspace.core.content import ConsiderationItemRef, Content
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.core.items import WorkspaceItem
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    WorkspaceBrowserIconKind,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog

TREE_INDENTATION_PX = Space.L
TREE_LABEL_COLUMN = 0
TREE_CONSIDERATION_COLUMN = 1
TREE_CONSIDERATION_COLUMN_WIDTH_PX = 24
TREE_ROW_ROLE = Qt.ItemDataRole.UserRole
TREE_SECTION_ROLE = Qt.ItemDataRole.UserRole + 1
TREE_HIDDEN_COUNT_ROLE = Qt.ItemDataRole.UserRole + 2
"""On a section row: how many excluded items its rows hide from the list."""


_OPEN_EDGE_PX = 2
_OPEN_TINT_ALPHA = 0.18
_PRESSED_ALPHA = 0.7
_SECTION_ARROW_PX = Space.L


@dataclass(frozen=True)
class _KindMark:
    """The icon a row kind shows, and the color it is drawn in."""

    icon: Icon
    color_source: KindColor | StatusColor

    def color(self, palette: QPalette) -> QColor:
        """Return the mark's color for the theme *palette* belongs to."""
        return self.color_source.color(palette)


# Each kind keeps one icon and color, so live streams, videos, and playlists read apart in a mixed workspace.
_KIND_MARKS: dict[WorkspaceBrowserIconKind, _KindMark] = {
    "live_video": _KindMark(Icon.LIVE_VIDEO, KindColor.LIVE),
    "video": _KindMark(Icon.VIDEO, KindColor.VIDEO),
    "playlist": _KindMark(Icon.PLAYLIST, KindColor.PLAYLIST),
    "overlay": _KindMark(Icon.OVERLAY, KindColor.OVERLAY),
    "unavailable": _KindMark(Icon.WARNING, StatusColor.WARNING),
    "pending": _KindMark(Icon.MORE, KindColor.MUTED),
}


def _has_open_row(rows: Sequence[WorkspaceBrowserRow]) -> bool:
    """Return whether any of *rows*, or their descendants, is open in a viewer."""
    return any(row.is_open or _has_open_row(row.children) for row in rows)


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
        section = index.data(TREE_SECTION_ROLE)
        if section is not None:
            self._paint_section(painter, option, section, index.data(TREE_HIDDEN_COUNT_ROLE) or 0)
            return
        row = _row_at(index)
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

    def _paint_section(
        self, painter: QPainter, option: QStyleOptionViewItem, section: WorkspaceBrowserSection, hidden_count: int
    ) -> None:
        """Draw a section caption: an arrow, its kind's color mark, the title, the count with any hidden items, and a
        rule to the right edge."""
        mark = _KIND_MARKS[section.kind.icon_kind].color(option.palette)
        hidden_note = f"  · {hidden_count} hidden" if hidden_count else ""
        text = f"{section.kind.title}  {len(section.rows)}{hidden_note}"
        # The arrow is the one the tree draws beside rows that have children.
        arrow = QStyleOptionViewItem(option)
        arrow.rect = QRect(option.rect.left(), option.rect.top(), _SECTION_ARROW_PX, option.rect.height())
        arrow.state = QStyle.StateFlag.State_Children | QStyle.StateFlag.State_Enabled
        if option.state & QStyle.StateFlag.State_Open:
            arrow.state |= QStyle.StateFlag.State_Open
        QApplication.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorBranch, arrow, painter)
        caption_rect = option.rect.adjusted(_SECTION_ARROW_PX - Space.S, 0, 0, 0)
        paint_section_caption(painter, caption_rect, option.palette, text, mark)


class _BrowserTree(QTreeWidget):
    """The content tree; a row open in a viewer gets a faint accent tint and an accent edge along the left side."""

    def drawRow(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Draw the row with a faint tint behind it, then its open edge."""
        is_open = index.data(TREE_SECTION_ROLE) is None and _row_at(index).is_open
        # The application palette has the accent; this tree's stylesheet-resolved palette does not.
        accent = QGuiApplication.palette().color(QPalette.ColorRole.Highlight)
        if is_open:
            tint = QColor(accent)
            tint.setAlphaF(_OPEN_TINT_ALPHA)
            painter.fillRect(option.rect, tint)
        super().drawRow(painter, option, index)
        if is_open:
            rect = option.rect
            edge = QRect(0, rect.top() + Space.XS, _OPEN_EDGE_PX, rect.height() - 2 * Space.XS)
            painter.fillRect(edge, accent)


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
    rows_changed = Signal()  # After set_browser_sections; see has_rows

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilContentBrowser")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._show_excluded = False
        self._search_query = ""
        self._folded: set[BrowserSection] = set()
        """The sections the user folded; a search unfolds them only while it is active."""
        self._revealed: set[BrowserSection] = set()
        """The folded sections an open row keeps unfolded, until that row closes or the user folds the section."""
        self._open_sections: set[BrowserSection] = set()
        self._applying_folds = False

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

    def set_browser_sections(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        """Render section headings and their explicit workspace item rows."""
        expansion = {
            self._row_data(item).row_id: (item.isExpanded(), self._row_data(item).is_open)
            for item in self._tree_items()
        }
        open_before = self._open_sections
        self._open_sections = {section.kind for section in sections if _has_open_row(section.rows)}
        self._tree.clear()
        for section in sections:
            heading = QTreeWidgetItem([section.kind.title, ""])
            heading.setData(TREE_LABEL_COLUMN, TREE_SECTION_ROLE, section)
            heading.setData(
                TREE_LABEL_COLUMN,
                Qt.ItemDataRole.AccessibleTextRole,
                f"{section.kind.title}, {len(section.rows)}",
            )
            heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._tree.addTopLevelItem(heading)
            heading.setFirstColumnSpanned(True)
            if not _has_open_row(section.rows):
                self._revealed.discard(section.kind)
            elif section.kind not in open_before:
                self._revealed.add(section.kind)
            with self._programmatic_folds():
                heading.setExpanded(section.kind not in self._folded or section.kind in self._revealed)
            for row in section.rows:
                item = self._create_tree_item(row)
                heading.addChild(item)
                self._install_tree_widgets(item)
                with self._programmatic_folds():
                    self._reveal_open_and_describe(item)
        for item in self._tree_items():
            row = self._row_data(item)
            if row.row_id in expansion:
                was_expanded, was_open = expansion[row.row_id]
                if was_open or not row.is_open:
                    item.setExpanded(was_expanded)
        self._apply_appearance()
        self._apply_visibility_filters()
        self.rows_changed.emit()

    def has_rows(self) -> bool:
        """Return whether the browser lists any workspace content."""
        return self._tree.topLevelItemCount() > 0

    def _tree_items(self) -> Iterator[QTreeWidgetItem]:
        """Iterate over all rows, including hidden and collapsed descendants."""
        iterator = QTreeWidgetItemIterator(self._tree)
        while (item := iterator.value()) is not None:
            if item.data(TREE_LABEL_COLUMN, TREE_ROW_ROLE) is not None:
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
        # Section rows sit undecorated at the root, so the rows under them indent as top-level rows used to; a section
        # paints its own arrow and a click on it folds it.
        self._tree.setRootIsDecorated(False)
        self._tree.itemClicked.connect(self._toggle_section)
        self._tree.itemExpanded.connect(self._remember_fold)
        self._tree.itemCollapsed.connect(self._remember_fold)
        self._tree.setItemDelegateForColumn(TREE_LABEL_COLUMN, _RowDelegate(self._tree))
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(TREE_LABEL_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(TREE_CONSIDERATION_COLUMN, QHeaderView.ResizeMode.Fixed)
        self._tree.setColumnWidth(TREE_CONSIDERATION_COLUMN, TREE_CONSIDERATION_COLUMN_WIDTH_PX)

    @contextmanager
    def _programmatic_folds(self) -> Iterator[None]:
        """Mark section folds changed by the browser itself, so only the user's are remembered."""
        self._applying_folds = True
        try:
            yield
        finally:
            self._applying_folds = False

    def _remember_fold(self, item: QTreeWidgetItem) -> None:
        """Remember that the user folded or unfolded a section."""
        section = item.data(TREE_LABEL_COLUMN, TREE_SECTION_ROLE)
        if section is None or self._applying_folds:
            return
        if item.isExpanded():
            self._folded.discard(section.kind)
        else:
            self._folded.add(section.kind)
            self._revealed.discard(section.kind)

    def _toggle_section(self, item: QTreeWidgetItem) -> None:
        """Fold or unfold a clicked section row."""
        if item.data(TREE_LABEL_COLUMN, TREE_SECTION_ROLE) is not None:
            item.setExpanded(not item.isExpanded())

    def _create_show_excluded_toggle(self) -> QToolButton:
        """Create the global toggle for showing or hiding excluded items."""
        toggle = QToolButton(self)
        toggle.setAutoRaise(True)
        toggle.setCheckable(True)
        toggle.setChecked(self._show_excluded)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setIcon(Icon.HIDDEN.icon())
        self._update_show_excluded_tooltip(toggle, self._show_excluded)
        toggle.toggled.connect(self._on_show_excluded_toggled)
        return toggle

    def _update_show_excluded_tooltip(self, toggle: QToolButton, show: bool) -> None:
        """Update the tooltip of the show-excluded browser filter."""
        if show:
            toggle.setToolTip("Showing excluded items. Click to hide them from this list.")
        else:
            toggle.setToolTip("Excluded items are hidden from this list. Click to show them.")

    def _on_show_excluded_toggled(self, show: bool) -> None:
        """Handle the global show-excluded toggle."""
        self._show_excluded = show
        self._update_show_excluded_tooltip(self._show_excluded_toggle, show)
        self._apply_visibility_filters()

    def _on_search_text_changed(self, text: str) -> None:
        """Filter workspace rows by their displayed names."""
        self._search_query = text.strip().casefold()
        self._apply_visibility_filters()

    def _apply_visibility_filters(self) -> None:
        """Show or hide items based on the search field and excluded-item toggle."""
        for i in range(self._tree.topLevelItemCount()):
            heading = self._tree.topLevelItem(i)
            if heading is None:
                continue
            visible = False
            for index in range(heading.childCount()):
                item = heading.child(index)
                if item is not None:
                    visible = self._apply_item_visibility(item, ancestor_matches=False) or visible
            hidden = 0 if self._show_excluded else self._excluded_rows_hidden(heading)
            # A search matching only excluded items keeps the heading, so its hidden count points to them.
            heading.setHidden(not visible and not hidden)
            kind = heading.data(TREE_LABEL_COLUMN, TREE_SECTION_ROLE).kind
            with self._programmatic_folds():
                heading.setExpanded(
                    bool(visible and self._search_query) or kind not in self._folded or kind in self._revealed
                )
            self._set_hidden_count(heading, hidden)

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

    def _excluded_rows_hidden(self, heading: QTreeWidgetItem) -> int:
        """Return how many rows under *heading* showing excluded items would reveal, the search still applied."""
        children = [heading.child(index) for index in range(heading.childCount())]
        return sum(
            self._visible_rows(child, ancestor_matches=False, show_excluded=True)
            - self._visible_rows(child, ancestor_matches=False, show_excluded=False)
            for child in children
            if child is not None
        )

    def _visible_rows(self, item: QTreeWidgetItem, *, ancestor_matches: bool, show_excluded: bool) -> int:
        """Return how many rows in *item*'s subtree, itself included, the filters would leave visible."""
        item_matches = self._item_matches_search(item)
        children = [item.child(index) for index in range(item.childCount())]
        below = sum(
            self._visible_rows(child, ancestor_matches=ancestor_matches or item_matches, show_excluded=show_excluded)
            for child in children
            if child is not None
        )
        row = self._row_data(item)
        passes_consideration = row.consideration_ref is None or row.is_considered or show_excluded
        passes_search = not self._search_query or ancestor_matches or item_matches or below > 0
        return 1 + below if passes_consideration and passes_search else 0

    def _set_hidden_count(self, heading: QTreeWidgetItem, hidden: int) -> None:
        """Record on a section row how many excluded items the list hides, for its caption and screen readers."""
        section: WorkspaceBrowserSection = heading.data(TREE_LABEL_COLUMN, TREE_SECTION_ROLE)
        heading.setData(TREE_LABEL_COLUMN, TREE_HIDDEN_COUNT_ROLE, hidden)
        hidden_note = f", {hidden} hidden" if hidden else ""
        text = f"{section.kind.title}, {len(section.rows)}{hidden_note}"
        heading.setData(TREE_LABEL_COLUMN, Qt.ItemDataRole.AccessibleTextRole, text)

    def _item_matches_search(self, item: QTreeWidgetItem) -> bool:
        """Return whether the tree item label matches the active search query."""
        return not self._search_query or self._search_query in self._row_data(item).search_text.casefold()

    def _apply_appearance(self) -> None:
        """Recolor row text and the pressed show-excluded button from the current palette."""
        pressed = palette_color_css(self.palette(), QPalette.ColorRole.Highlight, alpha=_PRESSED_ALPHA)
        self._show_excluded_toggle.setStyleSheet(
            f"""
            QToolButton {{ border: none; padding: 0px; margin: 0px; background: transparent; }}
            QToolButton:hover {{ background: transparent; }}
            QToolButton:checked {{ background: {pressed}; border-radius: {Radius.CONTROL}px; }}
            """
        )
        for item in self._tree_items():
            self._refresh_visual_state(item)

    def _create_tree_item(self, row: WorkspaceBrowserRow) -> QTreeWidgetItem:
        """Create a tree item for one explicit browser row."""
        item = QTreeWidgetItem([row.label, ""])
        item.setData(TREE_LABEL_COLUMN, TREE_ROW_ROLE, row)
        # The detail is painted beside the label; screen readers get it from here.
        detail = row.detail
        item.setData(
            TREE_LABEL_COLUMN, Qt.ItemDataRole.AccessibleTextRole, f"{row.label}, {detail}" if detail else row.label
        )
        for child_row in row.children:
            item.addChild(self._create_tree_item(child_row))
        return item

    def _row_data(self, item: QTreeWidgetItem) -> WorkspaceBrowserRow:
        """Return the stored row metadata for one tree item."""
        row = item.data(TREE_LABEL_COLUMN, TREE_ROW_ROLE)
        return cast(WorkspaceBrowserRow, row)

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
        mark = _KIND_MARKS[row.icon_kind]
        item.setIcon(TREE_LABEL_COLUMN, mark.icon.icon(mark.color(palette)))
        item.setForeground(TREE_LABEL_COLUMN, brush)
        for column in range(1, self._tree.columnCount()):
            item.setForeground(column, brush)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Open the double-clicked item; Ctrl+double-click opens it to the side."""
        if column == TREE_CONSIDERATION_COLUMN or item.data(TREE_LABEL_COLUMN, TREE_SECTION_ROLE) is not None:
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

    def _reveal_open_and_describe(self, item: QTreeWidgetItem) -> None:
        """Expand the path to an open row and give each row its hover text, then recurse into children."""
        row = self._row_data(item)
        is_open = row.is_open
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
        if item.data(TREE_LABEL_COLUMN, TREE_SECTION_ROLE) is not None:
            return menu
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
