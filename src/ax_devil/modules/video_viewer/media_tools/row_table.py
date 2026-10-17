"""A virtualized, sortable table of plain rows for the media tools pages.

``RowTableModel`` holds any objects as rows and a ``Column`` per cell says how to read, show and sort it.
``RowTableView`` paints every cell kind itself, one delegate and no widgets per row, so a list of thousands stays cheap
and updating it for every video frame does not rebuild it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chip import paint_tag
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import paint_row_background
from ax_devil.modules.chrome.theme import category_color
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole
from ax_devil.modules.video_viewer.media_tools.presence import Presence, paint_presence

_DIMMED_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_DIMMED_OPACITY = 0.5
_CHIP_ALPHA = 0.18
_RULE_ALPHA = 0.12
_NATURAL_PARTS = re.compile(r"(\d+)")


class CellKind(Enum):
    """How a column's cells are painted, and so what its ``value`` returns.

    ``TEXT`` and ``MONO`` take a ``str``, ``NUMBER`` a ``str`` shown right-aligned in the monospace face, ``ID_DOT`` and
    ``TAG`` a ``Labeled``, ``ID_CHIPS`` a sequence of ``Labeled``, ``PRESENCE`` a ``Presence``, and ``CHEVRON`` nothing.
    """

    ID_DOT = auto()
    TEXT = auto()
    MONO = auto()
    NUMBER = auto()
    PRESENCE = auto()
    TAG = auto()
    ID_CHIPS = auto()
    CHEVRON = auto()


@dataclass(frozen=True, slots=True)
class Labeled:
    """A *text* colored by the *category* it belongs to, such as an object's id by its class or an event's kind."""

    text: str
    category: str


@dataclass(frozen=True, slots=True)
class Column:
    """One column: its header *title*, how its cells are painted, and how to read them from a row.

    *value* returns what *cell* paints. *sort_key* makes the column sortable by the header, and returns values that
    compare with each other across rows; ``None`` leaves it unsortable. A *stretch* column shares the room the fixed
    ones leave, and the others are *width_chars* monospace characters wide (a default for their cell kind if ``None``).
    """

    title: str
    cell: CellKind
    value: Callable[[Any], Any]
    sort_key: Callable[[Any], Any] | None = None
    stretch: bool = False
    width_chars: int | None = None


_DEFAULT_CHARS = {
    CellKind.ID_DOT: 10,
    CellKind.TEXT: 10,
    CellKind.MONO: 10,
    CellKind.NUMBER: 6,
    CellKind.PRESENCE: 12,
    CellKind.TAG: 10,
    CellKind.ID_CHIPS: 14,
    CellKind.CHEVRON: 0,
}
_RIGHT_ALIGNED = frozenset({CellKind.NUMBER})


def _natural(text: str) -> tuple[tuple[int, str], ...]:
    """Return *text* as a sort key that orders embedded numbers by value, so ``"id9"`` comes before ``"id10"``."""
    return tuple((int(part), "") if part.isdigit() else (-1, part) for part in _NATURAL_PARTS.split(text))


class RowTableModel(QAbstractTableModel):
    """Plain objects as rows, shown through *columns*; rows are identified by a key, not by their position.

    Keys survive reordering, so the highlighted row, a page's origin row and the neighbors a page steps to follow the
    object, whatever the sort. Order is the sort column's, ties broken by key, or the order given if none is sorted.
    """

    def __init__(self, columns: Sequence[Column], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = tuple(columns)
        self._source: list[object] = []
        self._rows: list[object] = []
        self._keys: list[Hashable] = []
        self._key_of: Callable[[Any], Hashable] = id
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._highlight: Hashable | None = None
        self._dimmed: Callable[[Any], bool] | None = None

    @property
    def columns(self) -> tuple[Column, ...]:
        """Return the columns."""
        return self._columns

    def set_rows(self, rows: Sequence[object], key: Callable[[Any], Hashable]) -> None:
        """Show *rows*, each identified by ``key(row)``.

        The very same row objects again only repaint, without sorting; when the same keys come back in the same order
        the cells repaint too, so scroll position and keyboard cursor stay; the same keys in another order move the
        rows; otherwise the table resets.
        """
        self._key_of = key
        if len(rows) == len(self._source) and all(new is old for new, old in zip(rows, self._source)):
            self._repaint_all()
            return
        self._source = list(rows)
        self._apply_order()

    def row_for(self, key: Hashable) -> object | None:
        """Return the row with *key*, or ``None`` if there is none."""
        index = self.row_of(key)
        return self._rows[index] if index >= 0 else None

    def row_of(self, key: Hashable) -> int:
        """Return the position of the row with *key*, or -1."""
        return self._keys.index(key) if key in self._keys else -1

    def row_at(self, position: int) -> object:
        """Return the row at *position*."""
        return self._rows[position]

    def key_at(self, position: int) -> Hashable:
        """Return the key of the row at *position*."""
        return self._keys[position]

    def neighbor(self, key: Hashable, delta: int) -> Hashable | None:
        """Return the key of the row *delta* places from the row with *key* in the current order, or ``None`` if
        *key* is unknown or the step leaves the table.
        """
        position = self.row_of(key)
        target = position + delta
        return self._keys[target] if position >= 0 and 0 <= target < len(self._keys) else None

    def set_highlight(self, key: Hashable | None) -> None:
        """Mark the row with *key* as the current one, or none with ``None``."""
        previous, self._highlight = self._highlight, key
        for changed in (previous, key):
            self._repaint_row(self.row_of(changed) if changed is not None else -1)

    def highlight(self) -> Hashable | None:
        """Return the key of the highlighted row."""
        return self._highlight

    def is_active(self, position: int) -> bool:
        """Return whether the row at *position* is the highlighted one."""
        return (
            self._highlight is not None and 0 <= position < len(self._keys) and self._keys[position] == self._highlight
        )

    def set_dimmed(self, predicate: Callable[[Any], bool] | None) -> None:
        """Dim the rows for which *predicate* holds; ``None`` dims none."""
        self._dimmed = predicate
        self._repaint_all()

    def sort_state(self) -> tuple[int, Qt.SortOrder]:
        """Return the sorted column (-1 for none) and its order."""
        return self._sort_column, self._sort_order

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """Order the rows by *column*, or in the order given when *column* is -1 or has no sort key."""
        self._sort_column, self._sort_order = column, order
        self._apply_order()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802
        """Return the number of rows."""
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802
        """Return the number of columns."""
        return 0 if parent is not None and parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        """Return the cell's value for the display role, and whether its row is dimmed for the dimmed role."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return self._columns[index.column()].value(row)
        if role == _DIMMED_ROLE:
            return self._dimmed is not None and self._dimmed(row)
        return None

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = int(Qt.ItemDataRole.DisplayRole)
    ) -> Any:
        """Return a column's title."""
        if orientation == Qt.Orientation.Horizontal and role == int(Qt.ItemDataRole.DisplayRole):
            return self._columns[section].title
        return None

    def _apply_order(self) -> None:
        keyed = [(self._key_of(row), row) for row in self._source]
        sort_key = self._columns[self._sort_column].sort_key if self._sort_column >= 0 else None
        if sort_key is not None:
            # Python's sort is stable: order by key first, so equal sort values keep a fixed order from frame to frame.
            keyed.sort(key=lambda pair: _natural(str(pair[0])))
            keyed.sort(key=lambda pair: sort_key(pair[1]), reverse=self._sort_order == Qt.SortOrder.DescendingOrder)
        keys = [key for key, _ in keyed]
        rows = [row for _, row in keyed]
        if keys == self._keys:
            self._rows = rows
            self._repaint_all()
        elif len(set(keys)) == len(keys) == len(self._keys) and set(keys) == set(self._keys):
            self._move_rows(keys, rows)
        else:
            self.beginResetModel()
            self._keys, self._rows = keys, rows
            self.endResetModel()

    def _move_rows(self, keys: list[Hashable], rows: list[object]) -> None:
        self.layoutAboutToBeChanged.emit()
        old_keys = self._keys
        self._keys, self._rows = keys, rows
        new_position = {key: position for position, key in enumerate(keys)}
        old = self.persistentIndexList()
        self.changePersistentIndexList(
            old, [self.index(new_position[old_keys[index.row()]], index.column()) for index in old]
        )
        self.layoutChanged.emit()

    def _repaint_all(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, len(self._columns) - 1))

    def _repaint_row(self, position: int) -> None:
        if position >= 0:
            self.dataChanged.emit(self.index(position, 0), self.index(position, len(self._columns) - 1))


class _Header(QHeaderView):
    """Column headers in the caption style, the sorted one emphasised and marked with its direction."""

    def __init__(self, model: RowTableModel, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._table = model
        self.setSectionsMovable(False)
        self.setHighlightSections(False)
        self.setStretchLastSection(False)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:  # noqa: N802
        """Draw the title (elided to fit), the sort direction if this column is sorted, and a rule under the header."""
        column = self._table.columns[logicalIndex]
        sorted_column, order = self._table.sort_state()
        is_sorted = sorted_column == logicalIndex
        palette = self.palette()
        font = TextRole.CAPTION.font()
        painter.save()
        painter.setClipRect(rect)
        painter.setFont(font)
        text_color = palette.color(QPalette.ColorRole.Text if is_sorted else QPalette.ColorRole.PlaceholderText)
        painter.setPen(text_color)
        left_pad = Space.M if logicalIndex == 0 else Space.S
        inner = rect.adjusted(left_pad, 0, -Space.S, 0)
        arrow = TextRole.CAPTION.px + Space.XS if is_sorted else 0
        right_aligned = column.cell in _RIGHT_ALIGNED
        text_rect = inner.adjusted(0, 0, 0, 0)
        title = QFontMetrics(font).elidedText(column.title, Qt.TextElideMode.ElideRight, inner.width() - arrow)
        align = Qt.AlignmentFlag.AlignRight if right_aligned else Qt.AlignmentFlag.AlignLeft
        if right_aligned:
            text_rect.setRight(inner.right() - arrow)
        painter.drawText(text_rect, align | Qt.AlignmentFlag.AlignVCenter, title)
        if is_sorted:
            width = QFontMetrics(font).horizontalAdvance(title)
            size = TextRole.CAPTION.px
            x = inner.right() - size + 1 if right_aligned else inner.left() + width + Space.XS
            icon = (Icon.CHEVRON_UP if order == Qt.SortOrder.AscendingOrder else Icon.MENU).icon(text_color)
            icon.paint(painter, QRect(x, rect.top() + (rect.height() - size) // 2, size, size))
        rule = QColor(palette.color(QPalette.ColorRole.Text))
        rule.setAlphaF(_RULE_ALPHA)
        painter.fillRect(QRect(rect.left(), rect.bottom(), rect.width(), 1), rule)
        painter.restore()


class _CellDelegate(QStyledItemDelegate):
    """Paints every cell kind; the row's background is the view's."""

    def __init__(self, model: RowTableModel, parent: QWidget) -> None:
        super().__init__(parent)
        self._table = model
        self._painters: dict[CellKind, Callable[[QPainter, QRect, QPalette, Any, QColor], None]] = {
            CellKind.ID_DOT: self._paint_id_dot,
            CellKind.TEXT: self._paint_text,
            CellKind.MONO: self._paint_mono,
            CellKind.NUMBER: self._paint_number,
            CellKind.PRESENCE: self._paint_presence,
            CellKind.TAG: self._paint_tag,
            CellKind.ID_CHIPS: self._paint_chips,
            CellKind.CHEVRON: self._paint_chevron,
        }
        self.load_metrics()

    def load_metrics(self) -> None:
        """Read the fonts and sizes for the current text size."""
        self._body = TextRole.BODY.font()
        self._mono = TextRole.MONO.font()
        self._chip = TextRole.MONO_SMALL.font()
        self._row_height = Height.ROW.px
        self._dot = max(6, TextRole.BODY.px * 2 // 3)

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        """Return the uniform row height."""
        return QSize(option.rect.width(), self._row_height)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Paint one cell in the way its column's kind says."""
        column = self._table.columns[index.column()]
        # The application's palette: a view's own has the theme stylesheet's selection colors as its highlight.
        palette = QGuiApplication.palette()
        dimmed = bool(index.data(_DIMMED_ROLE))
        text = palette.color(QPalette.ColorRole.PlaceholderText if dimmed else QPalette.ColorRole.Text)
        rect = option.rect.adjusted(Space.M if index.column() == 0 else Space.S, 0, -Space.S, 0)
        painter.save()
        painter.setClipRect(option.rect)
        if dimmed:
            painter.setOpacity(_DIMMED_OPACITY)
        self._painters[column.cell](painter, rect, palette, index.data(), text)
        painter.restore()

    def _text(self, painter: QPainter, rect: QRect, font: Any, text: QColor, value: str, flags: int = 0) -> None:
        painter.setFont(font)
        painter.setPen(text)
        shown = QFontMetrics(font).elidedText(value, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, flags | int(Qt.AlignmentFlag.AlignVCenter), shown)

    def _paint_text(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, self._body, text, value, int(Qt.AlignmentFlag.AlignLeft))

    def _paint_mono(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, self._mono, text, value, int(Qt.AlignmentFlag.AlignLeft))

    def _paint_number(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, self._mono, text, value, int(Qt.AlignmentFlag.AlignRight))

    def _paint_id_dot(self, painter: QPainter, rect: QRect, palette: QPalette, value: Labeled, text: QColor) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(category_color(value.category, palette))
        painter.drawEllipse(QRect(rect.left(), rect.top() + (rect.height() - self._dot) // 2, self._dot, self._dot))
        after_dot = rect.adjusted(self._dot + Space.S, 0, 0, 0)
        self._text(painter, after_dot, self._mono, text, value.text, int(Qt.AlignmentFlag.AlignLeft))

    def _paint_presence(self, painter: QPainter, rect: QRect, palette: QPalette, value: Presence, text: QColor) -> None:
        paint_presence(
            painter,
            rect,
            palette,
            spans=value.spans,
            frame_count=value.frame_count,
            event_frames=value.event_frames,
            current_frame=value.current_frame,
            color=category_color(value.category, palette),
        )

    def _paint_tag(self, painter: QPainter, rect: QRect, palette: QPalette, value: Labeled, text: QColor) -> None:
        paint_tag(painter, rect, value.text, category_color(value.category, palette))

    def _paint_chips(
        self, painter: QPainter, rect: QRect, palette: QPalette, value: Sequence[Labeled], text: QColor
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._chip)
        metrics = QFontMetrics(self._chip)
        height = metrics.height() + Space.XS
        top = rect.top() + (rect.height() - height) // 2
        left = rect.left()
        for position, label in enumerate(value):
            width = metrics.horizontalAdvance(label.text) + 2 * Space.S
            more = f"+{len(value) - position}"
            if left + width > rect.right() and position > 0:
                painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
                painter.drawText(
                    QRect(left, rect.top(), rect.right() - left + 1, rect.height()),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    more,
                )
                return
            color = category_color(label.category, palette)
            fill = QColor(color)
            fill.setAlphaF(_CHIP_ALPHA)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            chip = QRect(left, top, min(width, rect.right() - left + 1), height)
            painter.drawRoundedRect(chip, Radius.CONTROL, Radius.CONTROL)
            painter.setPen(color)
            painter.drawText(
                chip,
                int(Qt.AlignmentFlag.AlignCenter),
                # One pixel of slack: Qt elides text whose advance is exactly the width it is given.
                metrics.elidedText(label.text, Qt.TextElideMode.ElideRight, chip.width() - 2 * Space.S + 1),
            )
            left += width + Space.S

    def _paint_chevron(self, painter: QPainter, rect: QRect, palette: QPalette, value: object, text: QColor) -> None:
        size = TextRole.SMALL.px
        icon = Icon.STEP_FORWARD.icon(palette.color(QPalette.ColorRole.PlaceholderText))
        icon.paint(painter, QRect(rect.right() - size + 1, rect.top() + (rect.height() - size) // 2, size, size))


class RowTableView(QTreeView):
    """A ``RowTableModel`` as a table of uniform rows with caption headers that sort their column.

    The highlighted row and the row under the pointer get the sidebar's row background. A click or Enter on a row emits
    ``rowActivated`` with the row object and a double click ``rowDoubleClicked``; the arrow keys move a keyboard
    cursor, and keys the table does not use (Esc, Backspace) go to the parent.
    """

    rowActivated = Signal(object)  # the row object
    rowDoubleClicked = Signal(object)  # the row object

    def __init__(self, model: RowTableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows_model = model
        self._hover_row = -1
        self._delegate = _CellDelegate(model, self)
        self.setModel(model)
        self.setHeader(_Header(model, self))
        self.setItemDelegate(self._delegate)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.viewport().setAutoFillBackground(False)
        header = self.header()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._sort_by_section)
        self._saved_scroll = 0
        model.modelAboutToBeReset.connect(self._save_scroll)
        model.modelReset.connect(self._restore_scroll)
        self.clicked.connect(self._activate_index)
        self.doubleClicked.connect(self._double_click_index)
        follow_appearance(self, self._apply_metrics)

    @property
    def rows_model(self) -> RowTableModel:
        """Return the model the table shows."""
        return self._rows_model

    def sort_by(self, column: int, order: Qt.SortOrder) -> None:
        """Sort by *column* in *order* and mark it in the header."""
        self._rows_model.sort(column, order)
        self.header().viewport().update()

    def scroll_to_key(self, key: Hashable) -> None:
        """Scroll the row with *key* into view."""
        position = self._rows_model.row_of(key)
        if position >= 0:
            self.scrollTo(self._rows_model.index(position, 0), QAbstractItemView.ScrollHint.EnsureVisible)

    def drawRow(  # noqa: N802
        self, painter: QPainter, options: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Draw the row's background across the whole row, then its cells."""
        row = index.row()
        cursor = row == self.currentIndex().row() and self.hasFocus()
        rect = QRect(0, options.rect.top(), self.viewport().width(), options.rect.height())
        paint_row_background(
            painter,
            rect,
            QGuiApplication.palette(),
            active=self._rows_model.is_active(row),
            hovered=row == self._hover_row or cursor,
        )
        plain = QStyleOptionViewItem(options)
        plain.state &= ~(
            QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus
        )
        super().drawRow(painter, plain, index)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Activate the cursor row on Enter."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self._activate_index(self.currentIndex())
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Follow the row under the pointer."""
        self._set_hover_row(self.indexAt(event.position().toPoint()).row())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Clear the hovered row."""
        self._set_hover_row(-1)
        super().leaveEvent(event)

    def _set_hover_row(self, row: int) -> None:
        if row != self._hover_row:
            self._hover_row = row
            self.viewport().update()

    def _activate_index(self, index: QModelIndex | QPersistentModelIndex) -> None:
        if 0 <= index.row() < self._rows_model.rowCount():
            self.rowActivated.emit(self._rows_model.row_at(index.row()))

    def _double_click_index(self, index: QModelIndex | QPersistentModelIndex) -> None:
        if 0 <= index.row() < self._rows_model.rowCount():
            self.rowDoubleClicked.emit(self._rows_model.row_at(index.row()))

    def _sort_by_section(self, section: int) -> None:
        if self._rows_model.columns[section].sort_key is None:
            return
        column, order = self._rows_model.sort_state()
        flipped = Qt.SortOrder.DescendingOrder if order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        self.sort_by(section, flipped if column == section else Qt.SortOrder.AscendingOrder)

    def _save_scroll(self) -> None:
        self._saved_scroll = self.verticalScrollBar().value()

    def _restore_scroll(self) -> None:
        self.doItemsLayout()
        self.verticalScrollBar().setValue(self._saved_scroll)

    def _apply_metrics(self) -> None:
        """Size rows, header and columns for the current text size."""
        self._delegate.load_metrics()
        header = self.header()
        header.setFixedHeight(Height.ROW.px)
        mono = QFontMetrics(TextRole.MONO.font())
        caption = QFontMetrics(TextRole.CAPTION.font())
        columns = self._rows_model.columns
        stretch_any = any(column.stretch for column in columns)
        for section, column in enumerate(columns):
            if column.stretch or (not stretch_any and section == len(columns) - 1):
                header.setSectionResizeMode(section, QHeaderView.ResizeMode.Stretch)
                continue
            pad = (Space.M if section == 0 else Space.S) + Space.S
            if column.cell is CellKind.CHEVRON:
                content = TextRole.SMALL.px
            else:
                chars = column.width_chars if column.width_chars is not None else _DEFAULT_CHARS[column.cell]
                content = chars * mono.averageCharWidth()
            title = caption.horizontalAdvance(column.title.upper()) + TextRole.CAPTION.px + Space.S
            header.setSectionResizeMode(section, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(section, max(content, title) + pad)
        self.doItemsLayout()
        self.viewport().update()
