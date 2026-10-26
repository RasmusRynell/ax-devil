"""The model behind a row table: rows identified by key, sorted, highlighted and dimmed."""

from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QWidget,
)

from ax_devil.modules.chrome.copy_cell import COPY_TOOLTIP
from ax_devil.modules.video_viewer.media_tools.row_columns import TEXT_ROLES, CellKind, Column

_NATURAL_PARTS = re.compile(r"(\d+)")


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
        if column.cell not in TEXT_ROLES or not rows:
            return False
        return not any(column.cell.text_of(column.value(row)) for row in self._rows[rows.start : rows.stop])

    def column_texts(self, section: int, rows: range) -> list[str]:
        """Return the text of the cell in *section* of each row at the positions *rows*; empty for kinds that show no
        single text.
        """
        column = self._columns[section]
        if column.cell not in TEXT_ROLES:
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
