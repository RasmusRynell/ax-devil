"""A virtualized, sortable table of plain rows for the media tools pages.

``RowTableModel`` holds any objects as rows and a ``Column`` per cell says how to read, show and sort it.
``RowTableView`` paints visible rows in one pass, with fixed geometry and no widgets per row. Columns take the width of
their longest visible value; when the view is too narrow they shrink (eliding text) and then the columns with the
highest ``priority`` number are hidden.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import cast

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QGuiApplication,
    QHelpEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
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
from ax_devil.modules.chrome.copy_cell import CopiedMark
from ax_devil.modules.chrome.list_row import paint_row_background
from ax_devil.modules.chrome.theme import FLUSH_VIEW
from ax_devil.modules.chrome.tokens import Height, Space
from ax_devil.modules.video_viewer.media_tools.column_sizing import ColumnSizer
from ax_devil.modules.video_viewer.media_tools.row_model import RowTableModel
from ax_devil.modules.video_viewer.media_tools.row_painting import RowHeader, RowPainter


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
        self._row_painter = RowPainter(model)
        self._sizer = ColumnSizer(model, self._row_painter, table_key)
        self._copied = CopiedMark(self, self._copied_changed)
        self.setModel(model)
        self._header = RowHeader(model, self)
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
        bar = self.verticalScrollBar()
        scroll = (
            bar.sizeHint().width() if self.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff else 0
        )
        return QSize(self._sizer.minimum_width() + scroll, super().minimumSizeHint().height())

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        """Fit the columns to the new size when the viewport is resized, and show a tooltip only for cut text."""
        if event.type() == QEvent.Type.Resize:
            self._sizer.measure_rows(self._visible_rows())
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
        self._sizer.measure_columns()
        self._sizer.measure_rows(self._visible_rows())
        self._layout_columns()
        self.updateGeometry()
        self.doItemsLayout()
        self.viewport().update()

    def _remeasure(self) -> None:
        """Measure the visible rows again, and fit the columns only if that changed what they need, so they do not
        move while the rows scroll past at the same widths.
        """
        if self._sizer.measure_rows(self._visible_rows()):
            self._layout_columns()

    def _values_changed(self) -> None:
        """Fit the columns again if one has started or stopped showing anything; value-only updates measure nothing."""
        if self._sizer.blanks_changed(self._visible_rows()):
            self._remeasure()

    def _visible_rows(self) -> range:
        """Return the positions of the rows in view, and one more above and below."""
        first = self.verticalScrollBar().value() // Height.ROW.px
        return range(first, min(first + self.viewport().height() // Height.ROW.px + 2, self._rows_model.rowCount()))

    def _layout_columns(self) -> None:
        """Hide the columns that do not fit and give the others their width. Hiding, not resetting the model, keeps
        the scroll position and cursor.
        """
        if self._laying_out or not self._sizer.measured:
            return
        widths = self._sizer.widths(self.viewport().width())
        last = self._sizer.real(sorted(widths))[-1]
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
        shown = [other for other in range(len(self._rows_model.columns)) if not self._header.isSectionHidden(other)]
        self._sizer.drag(section, old, new, shown, self.viewport().width())
        self._layout_columns()

    def _fit_section(self, section: int) -> None:
        """Make *section* as wide as its content."""
        self._sizer.fit_to_content(section)
        self._layout_columns()
