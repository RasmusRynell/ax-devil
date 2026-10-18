"""A virtualized, sortable table of plain rows for the media tools pages.

``RowTableModel`` holds any objects as rows and a ``Column`` per cell says how to read, show and sort it.
``RowTableView`` paints visible rows in one pass, with fixed geometry and no widgets per row. Columns take the width of
their longest visible value; when the view is too narrow they shrink (eliding text) and then the columns with the
highest ``priority`` number are hidden.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, cast

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
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QHelpEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QStyleOptionViewItem,
    QTableView,
    QToolTip,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chip import paint_tag
from ax_devil.modules.chrome.copy_cell import COPY_TOOLTIP, CopiedMark, copy_column_px, paint_copy_icon
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import paint_row_background
from ax_devil.modules.chrome.theme import FLUSH_VIEW, category_color
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.video_viewer.media_tools.presence import Presence, paint_presence

_DIMMED_OPACITY = 0.5
# The fewest characters of an id left when ids are shortened, before one is left out for "+n": the ellipsis and four
# visible characters on each side.
_MIN_ID_CHARS = 9
_RULE_ALPHA = 0.12
_DIVIDER_ALPHA = 0.15
_DIVIDER_ACTIVE_ALPHA = 0.6
_NATURAL_PARTS = re.compile(r"(\d+)")
# The widths columns were given by dragging, per table key and column title, for the rest of the session.
_REMEMBERED_WIDTHS: dict[str, dict[str, int]] = {}


class CellKind(Enum):
    """How a column's cells are painted, and so what its ``value`` returns.

    ``TEXT`` and ``MONO`` take a ``str``, ``NUMBER`` a ``str`` shown right-aligned in the monospace face, ``ID_DOT`` and
    ``TAG`` a ``Labeled``, ``ID_CHIPS`` a sequence of ``Labeled`` shown as plain ids, ``PRESENCE`` a ``Presence``.
    ``COPY`` is the copy icon the table adds itself (see ``RowTableModel``).
    """

    ID_DOT = auto()
    TEXT = auto()
    MONO = auto()
    NUMBER = auto()
    PRESENCE = auto()
    TAG = auto()
    ID_CHIPS = auto()
    COPY = auto()

    def text_of(self, value: Any) -> str | None:
        """Return the text of a cell of this kind holding *value*, or ``None`` if the cell shows no single text."""
        if self in (CellKind.ID_DOT, CellKind.TAG):
            return str(value.text)
        if self is CellKind.ID_CHIPS:
            return ", ".join(label.text for label in value)
        return str(value) if self in (CellKind.TEXT, CellKind.MONO, CellKind.NUMBER) else None


@dataclass(frozen=True, slots=True)
class Labeled:
    """A *text* colored by the *category* it belongs to, such as an object's id by its class or an event's kind."""

    text: str
    category: str


@dataclass(frozen=True, slots=True)
class Column:
    """One column: its header *title*, how its cells are painted, and how to read them from a row.

    *value* returns what *cell* paints. *sort_key* makes the column sortable by the header, and returns values that
    compare with each other across rows; ``None`` leaves it unsortable.

    A column is as wide as its longest visible value. A *stretch* column (the last one if none is) takes the room the
    others leave. When there is not enough room, text is elided at the end, or in the middle for ids and when *elide*
    says so, but only down to a comfortable width (20 characters for an id) while columns with a *priority* number above
    0 are still shown: those are hidden first, the highest number first, and a column that is empty in every visible row
    before that. Only when nothing is left to hide do columns shrink further, down to *width_chars* monospace characters
    (a default for their cell kind if ``None``).
    """

    title: str
    cell: CellKind
    value: Callable[[Any], Any]
    sort_key: Callable[[Any], Any] | None = None
    stretch: bool = False
    width_chars: int | None = None
    priority: int = 0
    elide: Qt.TextElideMode | None = None


# How many monospace characters of a cell are shown before columns are hidden, and at the very least (less if the
# values are shorter). A header title is never elided.
_COMFORTABLE_CHARS = {
    CellKind.ID_DOT: 20,
    CellKind.TEXT: 10,
    CellKind.MONO: 10,
    CellKind.NUMBER: 6,
    CellKind.PRESENCE: 12,
    CellKind.TAG: 10,
    CellKind.ID_CHIPS: 14,
}
_LEAST_CHARS = {
    CellKind.ID_DOT: 10,
    CellKind.TEXT: 8,
    CellKind.MONO: 6,
    CellKind.NUMBER: 4,
    CellKind.PRESENCE: 4,
    CellKind.TAG: 6,
    CellKind.ID_CHIPS: _MIN_ID_CHARS + 4,  # An id, then room for ", +n".
}
# The text role of the kinds that show a single text.
_TEXT_ROLES = {
    CellKind.TEXT: TextRole.BODY,
    CellKind.MONO: TextRole.MONO,
    CellKind.NUMBER: TextRole.MONO,
    CellKind.ID_DOT: TextRole.MONO,
    CellKind.ID_CHIPS: TextRole.MONO,
    CellKind.TAG: TextRole.SMALL,
}
_RIGHT_ALIGNED = frozenset({CellKind.NUMBER})


def _elide_mode(column: Column) -> Qt.TextElideMode:
    """Return where *column*'s text is cut: in the middle for ids, unless the column says otherwise."""
    if column.elide is not None:
        return column.elide
    return Qt.TextElideMode.ElideMiddle if column.cell is CellKind.ID_DOT else Qt.TextElideMode.ElideRight


def _pad(section: int) -> int:
    """Return the horizontal padding of a cell in *section*, both sides together."""
    return (Space.M if section == 0 else Space.S) + Space.S


def _natural(text: str) -> tuple[tuple[int, str], ...]:
    """Return *text* as a sort key that orders embedded numbers by value, so ``"id9"`` comes before ``"id10"``."""
    return tuple((int(part), "") if part.isdigit() else (-1, part) for part in _NATURAL_PARTS.split(text))


class RowTableModel(QAbstractTableModel):
    """Plain objects as rows, shown through *columns*; rows are identified by a key, not by their position.

    Keys survive reordering, so the highlighted row, a page's origin row and the neighbors a page steps to follow the
    object, whatever the sort. Order is the sort column's, ties broken by key, or the order given if none is sorted.

    With *copy_text*, a copy column is added after *columns*; it copies ``copy_text(row)``, the row's full text.
    """

    valuesChanged = Signal()  # The rows kept their keys but may show other values.

    def __init__(
        self,
        columns: Sequence[Column],
        parent: QWidget | None = None,
        *,
        copy_text: Callable[[Any], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._copy_section = len(columns) if copy_text is not None else -1
        self._columns = (*columns, Column("", CellKind.COPY, copy_text)) if copy_text is not None else tuple(columns)
        self._source: list[object] = []
        self._rows: list[object] = []
        self._keys: list[Hashable] = []
        self._order_keys: dict[Hashable, tuple[tuple[int, str], ...]] = {}
        self._key_of: Callable[[Any], Hashable] = id
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._highlight: Hashable | None = None
        self._dimmed: Callable[[Any], bool] | None = None

    @property
    def columns(self) -> tuple[Column, ...]:
        """Return the columns."""
        return self._columns

    @property
    def copy_section(self) -> int:
        """Return the copy column's position, or -1 if there is none."""
        return self._copy_section

    def copy_text_at(self, position: int) -> str | None:
        """Return the text the copy column copies for the row at *position*, or ``None`` without a copy column."""
        if self._copy_section < 0 or not 0 <= position < len(self._rows):
            return None
        return str(self._columns[self._copy_section].value(self._rows[position]))

    def is_blank(self, section: int, rows: range) -> bool:
        """Return whether *section* shows no text in the rows at the positions *rows*; a column of a kind without a
        single text never is.
        """
        column = self._columns[section]
        if column.cell not in _TEXT_ROLES or not rows:
            return False
        return not any(column.cell.text_of(column.value(row)) for row in self._rows[rows.start : rows.stop])

    def column_texts(self, section: int, rows: range) -> list[str]:
        """Return the text of the cell in *section* of each row at the positions *rows*; empty for kinds that show no
        single text.
        """
        column = self._columns[section]
        if column.cell not in _TEXT_ROLES:
            return []
        texts = (column.cell.text_of(column.value(row)) for row in self._rows[rows.start : rows.stop])
        return [text for text in texts if text is not None]

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

    def is_dimmed(self, position: int) -> bool:
        """Return whether the row at *position* is dimmed."""
        return self._dimmed is not None and self._dimmed(self._rows[position])

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
        """Return the cell's value for the display role, and the full text of a text cell for the tooltip role."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        column = self._columns[index.column()]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return column.value(row)
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return COPY_TOOLTIP if column.cell is CellKind.COPY else column.cell.text_of(column.value(row))
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
            previous = self._order_keys
            self._order_keys = {key: previous[key] if key in previous else _natural(str(key)) for key, _ in keyed}
            # Python's sort is stable: order by key first, so equal sort values keep a fixed order from frame to frame.
            keyed.sort(key=lambda pair: self._order_keys[pair[0]])
            keyed.sort(key=lambda pair: sort_key(pair[1]), reverse=self._sort_order == Qt.SortOrder.DescendingOrder)
        else:
            self._order_keys.clear()
        keys = [key for key, _ in keyed]
        rows = [row for _, row in keyed]
        if keys == self._keys:
            self._rows = rows
            self._repaint_all()
            self.valuesChanged.emit()
        elif len(set(keys)) == len(keys) == len(self._keys) and set(keys) == set(self._keys):
            self._move_rows(keys, rows)
        else:
            self.beginResetModel()
            self._keys, self._rows = keys, rows
            self.endResetModel()

    def _move_rows(self, keys: list[Hashable], rows: list[object]) -> None:
        # PySide exposes Qt signal overloads at runtime but omits __getitem__ from its stubs.
        self.layoutAboutToBeChanged["QList<QPersistentModelIndex>", "QAbstractItemModel::LayoutChangeHint"].emit(  # type: ignore[index]
            [], self.LayoutChangeHint.VerticalSortHint
        )
        old_keys = self._keys
        self._keys, self._rows = keys, rows
        new_position = {key: position for position, key in enumerate(keys)}
        old = self.persistentIndexList()
        self.changePersistentIndexList(
            old, [self.index(new_position[old_keys[index.row()]], index.column()) for index in old]
        )
        self.layoutChanged["QList<QPersistentModelIndex>", "QAbstractItemModel::LayoutChangeHint"].emit(  # type: ignore[index]
            [], self.LayoutChangeHint.VerticalSortHint
        )

    def _repaint_all(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, len(self._columns) - 1))

    def _repaint_row(self, position: int) -> None:
        if position >= 0:
            self.dataChanged.emit(self.index(position, 0), self.index(position, len(self._columns) - 1))


class _Header(QHeaderView):
    """Column headers in the caption style, the sorted one emphasised and marked with its direction.

    The divider after each resizable column is faint, brighter under the pointer and while it is being dragged.
    """

    def __init__(self, model: RowTableModel, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._table = model
        self._hover = -1
        self._pressed = -1
        self.setSectionsMovable(False)
        self.setHighlightSections(False)
        self.setStretchLastSection(False)
        self.setMouseTracking(True)

    @property
    def pressed_handle(self) -> int:
        """Return the section whose divider is being dragged, or -1."""
        return self._pressed

    def _handle_at(self, x: int) -> int:
        """Return the resizable section whose right-hand divider is at *x*, or -1."""
        for section in range(self.count()):
            if self.isSectionHidden(section) or self.sectionResizeMode(section) != QHeaderView.ResizeMode.Interactive:
                continue
            if abs(x - (self.sectionViewportPosition(section) + self.sectionSize(section))) <= Space.S:
                return section
        return -1

    def _set_hover(self, section: int) -> None:
        if section != self._hover:
            self._hover = section
            self.viewport().update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Note which divider a press starts dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self._handle_at(int(event.position().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Follow the divider under the pointer."""
        if self._pressed < 0:
            self._set_hover(self._handle_at(int(event.position().x())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """End a divider drag."""
        super().mouseReleaseEvent(event)
        self._pressed = -1
        self._set_hover(self._handle_at(int(event.position().x())))
        self.viewport().update()

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        """Clear the hovered divider when the pointer leaves."""
        if event.type() == QEvent.Type.Leave and self._pressed < 0:
            self._set_hover(-1)
        return super().viewportEvent(event)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:  # noqa: N802
        """Draw the title (elided to fit), the sort direction if this column is sorted, a rule under the header and
        the divider after the column.
        """
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
        if self.sectionResizeMode(logicalIndex) == QHeaderView.ResizeMode.Interactive:
            active = logicalIndex in (self._hover, self._pressed)
            divider = QColor(palette.color(QPalette.ColorRole.Text))
            divider.setAlphaF(_DIVIDER_ACTIVE_ALPHA if active else _DIVIDER_ALPHA)
            inset = Space.XS if active else Space.S
            painter.fillRect(QRect(rect.right(), rect.top() + inset, 1, rect.height() - 2 * inset), divider)
        painter.restore()


class _RowPainter:
    """Paint the cells of a visible row with shared appearance resources."""

    def __init__(self, model: RowTableModel) -> None:
        self._table = model
        # What the view tells about the pointer and the last copy, for the copy column's icon.
        self.hover_row = -1
        self.copied_key: Hashable | None = None
        # The last shown column, which gets more room on its right to keep its text clear of the edge and the copy icon.
        self.last_section = -1
        self.tail_px = 0
        # The row and the elision of the cell being painted, for the painters.
        self._row = -1
        self._elide = Qt.TextElideMode.ElideRight
        self._painters: dict[CellKind, Callable[[QPainter, QRect, QPalette, Any, QColor], None]] = {
            CellKind.ID_DOT: self._paint_id_dot,
            CellKind.TEXT: self._paint_text,
            CellKind.MONO: self._paint_mono,
            CellKind.NUMBER: self._paint_number,
            CellKind.PRESENCE: self._paint_presence,
            CellKind.TAG: self._paint_tag,
            CellKind.ID_CHIPS: self._paint_chips,
            CellKind.COPY: self._paint_copy,
        }
        self.load_metrics()

    def load_metrics(self) -> None:
        """Read the fonts and sizes for the current text size."""
        roles = set(_TEXT_ROLES.values())
        self._fonts = {role: role.font() for role in roles}
        self._metrics = {role: QFontMetrics(font) for role, font in self._fonts.items()}
        self._row_height = Height.ROW.px
        self._dot = max(6, TextRole.BODY.px * 2 // 3)
        self.tail_px = Space.S + TextRole.BODY.px // 3

    def chrome_px(self, kind: CellKind) -> int:
        """Return the width a cell of *kind* spends on anything but its text."""
        if kind is CellKind.ID_DOT:
            return self._dot + Space.S
        return 2 * Space.M if kind is CellKind.TAG else 0

    def shown_text(self, row: int, section: int, width: int) -> str | None:
        """Return the text of a cell as painted in a column *width* wide, or ``None`` if it has no text."""
        column = self._table.columns[section]
        full = column.cell.text_of(column.value(self._table.row_at(row)))
        if full is None:
            return None
        room = self._inner(QRect(0, 0, width, self._row_height), section, column.cell).width()
        return self._metrics[_TEXT_ROLES[column.cell]].elidedText(
            full, _elide_mode(column), max(0, room - self.chrome_px(column.cell))
        )

    def _inner(self, rect: QRect, section: int, kind: CellKind) -> QRect:
        """Return *rect* inside the cell padding; the copy cell has none."""
        if kind is CellKind.COPY:
            return rect
        right = Space.S + (self.tail_px if section == self.last_section else 0)
        return rect.adjusted(Space.M if section == 0 else Space.S, 0, -right, 0)

    def paint(self, painter: QPainter, rect: QRect, row: int, header: QHeaderView) -> None:
        """Read the row once and paint only columns that intersect the viewport."""
        model = self._table
        record = model.row_at(row)
        # The application's palette: a view's own has the theme stylesheet's selection colors as its highlight.
        palette = QGuiApplication.palette()
        dimmed = model.is_dimmed(row)
        text = palette.color(QPalette.ColorRole.PlaceholderText if dimmed else QPalette.ColorRole.Text)
        self._row = row
        for position, column in enumerate(model.columns):
            if header.isSectionHidden(position):
                continue
            cell = QRect(
                header.sectionViewportPosition(position), rect.top(), header.sectionSize(position), rect.height()
            )
            if not cell.intersects(rect):
                continue
            self._elide = _elide_mode(column)
            painter.save()
            painter.setClipRect(cell, Qt.ClipOperation.IntersectClip)
            if dimmed:
                painter.setOpacity(_DIMMED_OPACITY)
            self._painters[column.cell](
                painter, self._inner(cell, position, column.cell), palette, column.value(record), text
            )
            painter.restore()

    def _text(self, painter: QPainter, rect: QRect, role: TextRole, text: QColor, value: str, flags: int = 0) -> None:
        painter.setFont(self._fonts[role])
        painter.setPen(text)
        shown = self._metrics[role].elidedText(value, self._elide, rect.width())
        painter.drawText(rect, flags | int(Qt.AlignmentFlag.AlignVCenter), shown)

    def _paint_text(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, TextRole.BODY, text, value, int(Qt.AlignmentFlag.AlignLeft))

    def _paint_mono(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, TextRole.MONO, text, value, int(Qt.AlignmentFlag.AlignLeft))

    def _paint_number(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, TextRole.MONO, text, value, int(Qt.AlignmentFlag.AlignRight))

    def _paint_id_dot(self, painter: QPainter, rect: QRect, palette: QPalette, value: Labeled, text: QColor) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(category_color(value.category, palette))
        painter.drawEllipse(QRect(rect.left(), rect.top() + (rect.height() - self._dot) // 2, self._dot, self._dot))
        after_dot = rect.adjusted(self.chrome_px(CellKind.ID_DOT), 0, 0, 0)
        self._text(painter, after_dot, TextRole.MONO, text, value.text, int(Qt.AlignmentFlag.AlignLeft))

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
        """Draw the ids as muted text joined by commas: in full if they fit, else each shortened in the middle, else
        the first few and ``+n`` for the rest.
        """
        metrics = self._metrics[TextRole.MONO]
        ids = [label.text for label in value]
        separator = metrics.horizontalAdvance(", ")
        smallest = _MIN_ID_CHARS * metrics.horizontalAdvance("0")
        shown: list[str] = []
        for count in range(len(ids), 0, -1):
            more = f", +{len(ids) - count}" if count < len(ids) else ""
            room = rect.width() - metrics.horizontalAdvance(more) - separator * (count - 1)
            each = room // count
            if sum(metrics.horizontalAdvance(i) for i in ids[:count]) <= room:
                shown = ids[:count]
            elif count == 1 or each >= smallest:
                shown = [metrics.elidedText(i, Qt.TextElideMode.ElideMiddle, each) for i in ids[:count]]
            else:
                continue
            shown[-1] += more
            break
        painter.setFont(self._fonts[TextRole.MONO])
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), ", ".join(shown))

    def _paint_copy(self, painter: QPainter, rect: QRect, palette: QPalette, value: object, text: QColor) -> None:
        copied = self.copied_key is not None and self._table.key_at(self._row) == self.copied_key
        paint_copy_icon(painter, rect, palette, bright=self._row == self.hover_row, copied=copied)


@dataclass(frozen=True, slots=True)
class _ColumnFit:
    """What a column's width depends on besides its values, measured for the current fonts."""

    metrics: QFontMetrics | None  # The face of its text; ``None`` for a kind without a single text.
    chrome: int  # The width of a cell spent on anything but its text.
    title: int  # The width of the whole header title.
    comfortable: int  # The width of its text before other columns are hidden, if the values are long enough.
    minimum: int  # The width of its fewest characters.


class RowTableView(QTableView):
    """A ``RowTableModel`` as a table of uniform rows with caption headers that sort their column.

    The highlighted row and the row under the pointer get the sidebar's row background. A click or Enter on a row emits
    ``rowActivated`` with the row object; the arrow keys move a keyboard cursor, and keys the table does not use (Esc,
    Backspace) go to the parent.

    Dragging the divider after a column sets its width, and double-clicking it fits the column to its content. With a
    *table_key*, the widths are remembered for the session by every table of that key. The model's copy column, if it
    has one, copies on a click, and Ctrl+C copies the same for the cursor row.
    """

    rowActivated = Signal(object)  # the row object
    sortRequested = Signal()  # Let the page catch up to its pending snapshot before sorting.

    def __init__(self, model: RowTableModel, parent: QWidget | None = None, *, table_key: str | None = None) -> None:
        super().__init__(parent)
        self._rows_model = model
        self._hover_row = -1
        self._laying_out = False
        self._fits: list[_ColumnFit] = []  # Per column: what its width depends on besides its values.
        self._minimum: list[int] = []  # Per column: the narrowest it is for the minimum size; independent of the rows.
        self._natural: list[int] = []  # Per column: its width for its longest visible value.
        self._floor: list[int] = []  # Per column: the narrowest it is before other columns are hidden.
        self._least: list[int] = []  # Per column: the narrowest it is shrunk to for its rows.
        self._empty: list[bool] = []  # Per column: whether it shows nothing in any visible row.
        self._tail = (
            -1
        )  # The last shown column; it is wider by the delegate's tail_px, to keep its text off the right edge.
        self._user_widths = _REMEMBERED_WIDTHS.setdefault(table_key, {}) if table_key is not None else {}
        self._row_painter = _RowPainter(model)
        self._copied = CopiedMark(self, self._copied_changed)
        self.setModel(model)
        self._header = _Header(model, self)
        self.setHorizontalHeader(self._header)
        self.verticalHeader().hide()
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().setDefaultSectionSize(Height.ROW.px)
        self.setShowGrid(False)
        self.setStyleSheet(FLUSH_VIEW)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setTabKeyNavigation(False)
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
        header.sectionResized.connect(self._section_dragged)
        header.sectionHandleDoubleClicked.connect(self._fit_section)
        self._saved_scroll = 0
        model.modelAboutToBeReset.connect(self._save_scroll)
        model.modelReset.connect(self._restore_scroll)
        model.modelReset.connect(self._remeasure)
        model.layoutChanged.connect(self._remeasure)
        model.valuesChanged.connect(self._values_changed)
        self.verticalScrollBar().valueChanged.connect(lambda _: self._remeasure())
        self.clicked.connect(self._activate_index)
        follow_appearance(self, self._apply_metrics)

    def header(self) -> QHeaderView:
        """Return the flat table's horizontal header."""
        return self.horizontalHeader()

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

    def shown_text(self, row: int, section: int) -> str | None:
        """Return the text of a cell as it is painted now, elided to its column, or ``None`` if it shows no text."""
        if self.isColumnHidden(section):
            return None
        return self._row_painter.shown_text(row, section, self.columnWidth(section))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the width of the columns that are never hidden, at their narrowest, and of the scroll bar.

        It depends on the columns and the text size only, never on the rows, so the panel around the table keeps
        its width as contents change.
        """
        never_hidden = [
            self._minimum[section]
            for section, column in enumerate(self._rows_model.columns)
            if column.priority == 0 or column.stretch or column.cell is CellKind.COPY
        ]
        bar = self.verticalScrollBar()
        scroll = (
            bar.sizeHint().width() if self.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff else 0
        )
        tail = self._row_painter.tail_px
        return QSize(sum(never_hidden) + tail + scroll, super().minimumSizeHint().height())

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        """Fit the columns to the new size when the viewport is resized, and show a tooltip only for cut text."""
        if event.type() == QEvent.Type.Resize:
            self._measure_rows()
            self._layout_columns()
        elif event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(cast(QHelpEvent, event).pos())
            if index.isValid():
                full = self._rows_model.columns[index.column()].cell.text_of(index.data())
                if full is not None and self.shown_text(index.row(), index.column()) == full:
                    QToolTip.hideText()
                    return True
        return super().viewportEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Paint visible fixed-height rows once using native table navigation and geometry."""
        first = self.rowAt(event.rect().top())
        if first < 0:
            return
        painter = QPainter(self.viewport())
        painter.setClipRegion(event.region())
        for position in range(first, self._rows_model.rowCount()):
            top = self.rowViewportPosition(position)
            if top > event.rect().bottom():
                break
            options = QStyleOptionViewItem()
            options.rect = QRect(0, top, self.viewport().width(), self.rowHeight(position))
            self.drawRow(painter, options, self._rows_model.index(position, 0))
        painter.end()

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
        self._row_painter.paint(painter, rect, row, self.header())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Activate the cursor row on Enter, and copy it on Ctrl+C."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self._activate_row(self.currentIndex().row())
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Copy) and self._copy_row(self.currentIndex().row()):
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
            self._hover_row = self._row_painter.hover_row = row
            self.viewport().update()

    def _activate_index(self, index: QModelIndex | QPersistentModelIndex) -> None:
        if index.column() == self._rows_model.copy_section:
            self._copy_row(index.row())
        else:
            self._activate_row(index.row())

    def _activate_row(self, row: int) -> None:
        if 0 <= row < self._rows_model.rowCount():
            self.rowActivated.emit(self._rows_model.row_at(row))

    def _copy_row(self, row: int) -> bool:
        """Copy the full text of the row at *row* and show the check for a moment; return whether there was any."""
        text = self._rows_model.copy_text_at(row)
        if text is None:
            return False
        self._copied.copy(text, self._rows_model.key_at(row))
        return True

    def _copied_changed(self) -> None:
        self._row_painter.copied_key = self._copied.key
        self.viewport().update()

    def _sort_by_section(self, section: int) -> None:
        if self._rows_model.columns[section].sort_key is None:
            return
        self.sortRequested.emit()
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
        self._row_painter.load_metrics()
        self.verticalHeader().setDefaultSectionSize(Height.ROW.px)
        header = self.header()
        header.setFixedHeight(Height.ROW.px)
        header.setMinimumSectionSize(Space.S)
        self._measure_columns()
        self._measure_rows()
        self._layout_columns()
        self.updateGeometry()
        self.doItemsLayout()
        self.viewport().update()

    def _remeasure(self) -> None:
        """Measure the visible rows again, and fit the columns only if that changed what they need, so they do not
        move while the rows scroll past at the same widths.
        """
        before = (self._natural, self._floor, self._least, self._empty)
        self._measure_rows()
        if (self._natural, self._floor, self._least, self._empty) != before:
            self._layout_columns()

    def _values_changed(self) -> None:
        """Fit the columns again if one has started or stopped showing anything; value-only updates measure nothing."""
        rows = self._visible_rows()
        if any(self._rows_model.is_blank(section, rows) != blank for section, blank in enumerate(self._empty)):
            self._remeasure()

    def _visible_rows(self) -> range:
        """Return the positions of the rows in view, and one more above and below."""
        first = self.verticalScrollBar().value() // Height.ROW.px
        return range(first, min(first + self.viewport().height() // Height.ROW.px + 2, self._rows_model.rowCount()))

    def _real(self, shown: Sequence[int]) -> list[int]:
        """Return the sections among *shown* that are not the copy column."""
        return [section for section in shown if self._rows_model.columns[section].cell is not CellKind.COPY]

    def _absorber_of(self, shown: Sequence[int]) -> int:
        """Return the shown column that takes the room the others leave: the first stretch column that has no width
        of its own, else the last column.
        """
        columns = self._rows_model.columns
        real = self._real(shown)
        free = (
            section for section in real if columns[section].stretch and columns[section].title not in self._user_widths
        )
        return next(free, real[-1])

    def _measure_columns(self) -> None:
        """Measure what a column's width depends on besides its values: the width of its title, and its narrowest
        width for the minimum size, which comes from the column definitions and fonts alone: *width_chars* characters
        or the title, whichever is wider. Values never enter it, so it does not change as rows come and go.
        """
        caption = QFontMetrics(TextRole.CAPTION.font())
        self._fits, self._minimum = [], []
        for section, column in enumerate(self._rows_model.columns):
            if column.cell is CellKind.COPY:
                self._fits.append(_ColumnFit(None, 0, 0, 0, copy_column_px()))
                self._minimum.append(copy_column_px())
                continue
            chrome = self._row_painter.chrome_px(column.cell)
            # The whole title, and the arrow of a sortable column, with a pixel of slack against eliding.
            title = caption.horizontalAdvance(column.title.upper()) + 1
            if column.sort_key is not None:
                title += TextRole.CAPTION.px + Space.XS
            least_chars = column.width_chars if column.width_chars is not None else _LEAST_CHARS[column.cell]
            role = _TEXT_ROLES.get(column.cell, TextRole.MONO)
            metrics = QFontMetrics(role.font())
            char = metrics.horizontalAdvance("0") if role is TextRole.MONO else metrics.averageCharWidth()
            # One pixel of slack: Qt elides text whose advance is exactly the width it is given.
            least = math.ceil(least_chars * char) + chrome + 1
            comfortable = math.ceil(max(_COMFORTABLE_CHARS[column.cell], least_chars) * char) + chrome + 1
            self._fits.append(
                _ColumnFit(metrics if column.cell in _TEXT_ROLES else None, chrome, title, comfortable, least)
            )
            self._minimum.append(max(least, title) + _pad(section))

    def _measure_rows(self) -> None:
        """Measure the columns for the rows in view: their width for the longest value, the width below which other
        columns are hidden, the narrowest each is shrunk to, and whether it shows nothing at all.
        """
        if not self._fits:
            return
        rows = self._visible_rows()
        self._natural, self._floor, self._least, self._empty = [], [], [], []
        for section, (column, fit) in enumerate(zip(self._rows_model.columns, self._fits)):
            if column.cell is CellKind.COPY:
                for sizes in (self._natural, self._floor, self._least):
                    sizes.append(fit.minimum)
                self._empty.append(False)
                continue
            comfortable = fit.comfortable
            if fit.metrics is None:
                longest = comfortable
            else:
                texts = self._rows_model.column_texts(section, rows)
                longest = max((fit.metrics.horizontalAdvance(text) + 1 for text in texts), default=0) + fit.chrome
                if column.priority > 0 and not column.stretch:  # Shown whole, or hidden.
                    comfortable = longest
            pad = _pad(section)
            self._natural.append(max(longest, fit.title) + pad)
            self._floor.append(max(min(comfortable, longest), fit.title) + pad)
            self._least.append(min(self._minimum[section], self._floor[-1]))
            self._empty.append(self._rows_model.is_blank(section, rows))

    def _pref(self, section: int) -> int:
        """Return the width *section* would like: the one the user gave it, else the width of its content."""
        tail = self._row_painter.tail_px if section == self._tail else 0
        saved = self._user_widths.get(self._rows_model.columns[section].title)
        # A width saved at another text size may be below what the column now needs at the very least.
        return self._natural[section] + tail if saved is None else max(saved, self._least[section] + tail)

    def _floor_of(self, section: int) -> int:
        """Return the width below which *section* is not shrunk while other columns can be hidden."""
        return min(
            self._floor[section] + (self._row_painter.tail_px if section == self._tail else 0), self._pref(section)
        )

    def _least_of(self, section: int) -> int:
        """Return the narrowest *section* is ever shrunk to."""
        return min(
            self._least[section] + (self._row_painter.tail_px if section == self._tail else 0), self._pref(section)
        )

    def _fit(self, preferred: dict[int, int], room: int, floors: dict[int, int]) -> dict[int, int]:
        """Return *preferred* widths, or all shrunk toward their *floors* in proportion to what they can give up if
        they do not fit in *room*.
        """
        wanted = sum(preferred.values())
        if wanted <= room:
            return preferred
        bottom = sum(floors[section] for section in preferred)
        slack = max(0, room - bottom)
        return {
            section: floors[section] + (width - floors[section]) * slack // max(1, wanted - bottom)
            for section, width in preferred.items()
        }

    def _column_widths(self, available: int) -> dict[int, int]:
        """Return the width of every column that is shown in *available* pixels; the others are hidden.

        Columns that show nothing go first, then those with the highest priority number, until the rest fit at their
        comfortable width. A column the user gave a width keeps it as long as it fits; when it does not, columns
        without one shrink first, then the others. The saved width is only a preference and returns with room.
        """
        columns = self._rows_model.columns
        copy = self._rows_model.copy_section
        shown = list(range(len(columns)))

        def wanted() -> int:
            absorber = self._absorber_of(shown)
            return sum(self._floor_of(s) if s in (copy, absorber) else self._pref(s) for s in shown)

        def point_tail() -> None:
            self._tail = self._real(shown)[-1]

        def hide(candidates: list[int]) -> None:
            shown.remove(max(candidates, key=lambda section: (columns[section].priority, section)))
            point_tail()

        def hideable() -> list[int]:
            return [s for s in shown if columns[s].priority > 0 and not columns[s].stretch and s != copy]

        point_tail()
        while wanted() > available and any(self._empty[s] for s in hideable()):
            hide([s for s in hideable() if self._empty[s]])
        while sum(self._floor_of(s) for s in shown) > available and hideable():
            hide(hideable())
        copy_px = copy_column_px() if copy in shown else 0
        absorber = self._absorber_of(shown)
        real = self._real(shown)
        floors = {section: self._floor_of(section) for section in real}
        if sum(floors.values()) > available - copy_px:  # Nothing is left to hide.
            least = {section: self._least_of(section) for section in real}
            widths = self._fit(floors, available - copy_px, least)
        else:
            others = [section for section in real if section != absorber]
            own = {section: self._pref(section) for section in others if columns[section].title in self._user_widths}
            spare = available - copy_px - floors[absorber]
            auto = self._fit(
                {section: self._pref(section) for section in others if section not in own},
                spare - sum(own.values()),
                floors,
            )
            widths = {**auto, **self._fit(own, spare - sum(auto.values()), floors), absorber: floors[absorber]}
        widths[absorber] += max(0, available - copy_px - sum(widths.values()))
        if copy in shown:
            widths[copy] = copy_column_px()
        return widths

    def _layout_columns(self) -> None:
        """Hide the columns that do not fit and give the others their width. Hiding, not resetting the model, keeps
        the scroll position and cursor.
        """
        if self._laying_out or not self._minimum:
            return
        widths = self._column_widths(self.viewport().width())
        last = self._real(sorted(widths))[-1]
        self._row_painter.last_section = last
        header = self.header()
        self._laying_out = True
        try:
            for section in range(len(self._rows_model.columns)):
                if header.isSectionHidden(section) == (section in widths):
                    header.setSectionHidden(section, section not in widths)
                fixed = section in (last, self._rows_model.copy_section)
                header.setSectionResizeMode(
                    section, QHeaderView.ResizeMode.Fixed if fixed else QHeaderView.ResizeMode.Interactive
                )
                if section in widths:
                    header.resizeSection(section, widths[section])
        finally:
            self._laying_out = False
        self.viewport().update()

    def _section_dragged(self, section: int, old: int, new: int) -> None:
        """Remember the width the user dragged *section* to, as far as the other columns leave room: only the width
        that could be applied, and nothing if there was no room to change it.
        """
        if self._laying_out or self._header.pressed_handle != section:
            return
        columns = self._rows_model.columns
        title = columns[section].title
        saved = self._user_widths.get(title)
        shown = [other for other in range(len(columns)) if not self._header.isSectionHidden(other)]
        self._user_widths[title] = new  # Provisionally, to know which column then takes the leftover.
        copy_px = copy_column_px() if self._rows_model.copy_section in shown else 0
        reserved = copy_px + sum(self._floor_of(other) for other in self._real(shown) if other != section)
        applied = max(self._least[section], min(new, self.viewport().width() - reserved))
        if applied == old and saved is None:
            del self._user_widths[title]
        else:
            self._user_widths[title] = applied
        self._layout_columns()

    def _fit_section(self, section: int) -> None:
        """Make *section* as wide as its content."""
        self._user_widths[self._rows_model.columns[section].title] = self._natural[section]
        self._layout_columns()
