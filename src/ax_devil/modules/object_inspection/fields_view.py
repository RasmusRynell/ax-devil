"""A foldable key/value tree for an object's fields and events, painted by one delegate with no widget per row.

``Section`` groups ``Row`` objects under a caption. ``FieldsModel`` holds the sections, so a frame's new values repaint
the rows in place while the structure stays; ``FieldsView`` remembers which sections the user folded. Rows without a
section (the object's id) come first. Every row ends in the copy column shared with the lists, and the label column is
as wide as the longest label of the whole view, so values line up across sections.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
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
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QTreeView,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.copy_cell import COPY_TOOLTIP, CopiedMark, copy_column_px, paint_copy_icon
from ax_devil.modules.chrome.list_row import paint_row_background
from ax_devil.modules.chrome.section_caption import paint_section_caption
from ax_devil.modules.chrome.theme import FLUSH_VIEW, category_color
from ax_devil.modules.chrome.tokens import Height, Space
from ax_devil.modules.object_inspection.fields_model import FieldsModel, Section
from ax_devil.modules.object_inspection.rows import Row, RowFonts

_ARROW_PX = Space.M  # Rows and captions start here, so their text lines up with other content at Space.M plus this.
_VALUE_CHARS = 14  # The characters of a value, such as an id, that stay visible however narrow the view is.
_ID_CHARS = 20  # The characters of a long value, such as an id, that the label column leaves where it can.
_LABEL_CHARS = 14  # The characters of a label that stay visible; a longer label is cut in the middle at the narrowest.


def _row_area(rect: QRect) -> QRect:
    """Return the part of a row's *rect* its content gets: after the fold arrow's indent and before the copy column."""
    return rect.adjusted(_ARROW_PX, 0, -(copy_column_px() + Space.S), 0)


class _Delegate(QStyledItemDelegate):
    """Paints section captions and asks each row to paint itself, then adds the copy column."""

    def __init__(self, view: QTreeView, model: FieldsModel) -> None:
        super().__init__(view)
        self._view = view
        self._fields = model
        # What the view tells about the pointer, the last copy and the label column.
        self.hover = QPersistentModelIndex()
        self.copied: CopiedMark | None = None
        self.label_width = 0
        self.load_metrics()

    def load_metrics(self) -> None:
        """Read the fonts and row height for the current text size."""
        self.fonts = RowFonts.current()
        self._height = Height.ROW.px

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        """Return the uniform row height."""
        return QSize(option.rect.width(), self._height)

    def helpEvent(  # noqa: N802
        self,
        event: QHelpEvent,
        view: QAbstractItemView,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        """Show the full text of a row with something cut off, or ``Copy`` over the copy icon."""
        if event.type() != QEvent.Type.ToolTip:
            return super().helpEvent(event, view, option, index)
        text = None
        row = self._fields.row_at(index)
        if self._fields.has_copy_at(index) and event.pos().x() > option.rect.right() - copy_column_px():
            text = COPY_TOOLTIP
        elif row is not None:
            text = row.cut_text(_row_area(option.rect), self.fonts, self.label_width)
        if text is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(event.globalPos(), text, view)
        return True

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Paint a section's caption with its fold arrow, or a row inside the caption's indent; both end in the copy
        column.
        """
        row, section = self._fields.row_at(index), self._fields.section_at(index)
        palette = QGuiApplication.palette()  # The view's own palette has the theme's selection colors.
        has_copy = self._fields.has_copy_at(index)
        copy_px = copy_column_px()
        painter.save()
        painter.setClipRect(option.rect)
        body = option.rect.adjusted(0, 0, -copy_px if has_copy else 0, 0)
        if row is not None:
            row.paint(painter, _row_area(option.rect), palette, self.fonts, self.label_width)
        elif section is not None:
            self._paint_section(painter, option, body, palette, section, self._view.isExpanded(index))
        if has_copy:
            copied = self.copied is not None and self.copied.key == self._fields.copy_key_at(index)
            copy_rect = QRect(option.rect.right() - copy_px + 1, option.rect.top(), copy_px, option.rect.height())
            paint_copy_icon(painter, copy_rect, palette, bright=index == self.hover, copied=copied)
        painter.restore()

    def _paint_section(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        rect: QRect,
        palette: QPalette,
        section: Section,
        expanded: bool,
    ) -> None:
        arrow = QStyleOptionViewItem()
        arrow.palette = palette
        arrow.rect = QRect(rect.left(), rect.top(), _ARROW_PX, rect.height())
        arrow.state = QStyle.StateFlag.State_Children | QStyle.StateFlag.State_Enabled
        if expanded:
            arrow.state |= QStyle.StateFlag.State_Open
        widget = option.widget
        style = widget.style() if widget is not None else QGuiApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorBranch, arrow, painter, widget)
        paint_section_caption(
            painter,
            rect.adjusted(_ARROW_PX - Space.S, 0, 0, 0),
            palette,
            f"{section.name}  {len(section.rows)}",
            category_color(section.name, palette),
        )


class FieldsView(QTreeView):
    """``Section`` rows as a tree of uniform rows; clicking a caption folds its section.

    Which sections are folded is kept by section name in the dictionary the owner passes in, so pages for different
    objects fold alike. Sections start as their ``folded_by_default`` says until the user folds one. A click on a row's
    copy icon, or Ctrl+C on the current row, copies it, and a click on an event row emits ``eventActivated``.
    """

    eventActivated = Signal(object)  # FrameEvent

    def __init__(self, fold_state: dict[str, bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fold_state = fold_state
        self._syncing = False
        self._hover = QPersistentModelIndex()
        self._fields_model = FieldsModel(self)
        self._delegate = _Delegate(self, self._fields_model)
        self._longest_label = 0
        self._widest_value = 0
        self._widest_event = 0
        self._measured_for = -1  # The model structure the label width was measured for.
        self._values_measured = False
        self._copied = CopiedMark(self, lambda: self.viewport().update())
        self._delegate.copied = self._copied
        self.setModel(self._fields_model)
        self.setItemDelegate(self._delegate)
        self.setHeaderHidden(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setItemsExpandable(True)
        self.setExpandsOnDoubleClick(False)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        # The rows paint their own background and focus mark; the theme's item rules would draw another.
        self.setStyleSheet(f"QTreeView, QTreeView::item {{ background: transparent; border: none; }} {FLUSH_VIEW}")
        self.viewport().setAutoFillBackground(False)
        self.clicked.connect(self._on_clicked)
        self.expanded.connect(lambda index: self._remember_fold(index, folded=False))
        self.collapsed.connect(lambda index: self._remember_fold(index, folded=True))
        follow_appearance(self, self._apply_metrics)

    def set_sections(self, sections: Sequence[Section], lead: Sequence[Row] = ()) -> None:
        """Show *sections* under the *lead* rows, keeping the scroll position and the sections' fold state."""
        scroll = self.verticalScrollBar().value()
        was_reset = self._fields_model.set_sections(sections, lead)
        self._values_measured = False
        self._fit_labels()
        self._sync_folds()
        if was_reset:
            self.doItemsLayout()
            self.verticalScrollBar().setValue(scroll)

    def drawRow(  # noqa: N802
        self, painter: QPainter, options: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Draw the row's background across the whole row, then its cell."""
        rect = QRect(0, options.rect.top(), self.viewport().width(), options.rect.height())
        paint_row_background(
            painter,
            rect,
            QGuiApplication.palette(),
            active=self.selectionModel().isSelected(index),
            hovered=index == self._hover or (index == self.currentIndex() and self.hasFocus()),
        )
        plain = QStyleOptionViewItem(options)
        plain.state &= ~(
            QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus
        )
        super().drawRow(painter, plain, index)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Copy the current row on Ctrl+C; fold a section or open an event on Enter and Space."""
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy(self.currentIndex())
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.currentIndex().isValid():
            self._on_clicked(self.currentIndex())
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Copy on a press in the copy column, before it selects or folds anything."""
        position = event.position().toPoint()
        index = self.indexAt(position)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and position.x() >= self.viewport().width() - copy_column_px()
            and self._copy(index)
        ):
            self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Follow the row under the pointer."""
        self._set_hover(self.indexAt(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Clear the hovered row."""
        self._set_hover(QModelIndex())
        super().leaveEvent(event)

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        """Refit the label column when the viewport is resized."""
        if event.type() == QEvent.Type.Resize:
            self._fit_labels()
        return super().viewportEvent(event)

    def _copy(self, index: QModelIndex | QPersistentModelIndex) -> bool:
        """Copy the row or caption at *index* and show the check for a moment; return whether it has anything."""
        text = self._fields_model.copy_text_at(index)
        if text is None:
            return False
        self._copied.copy(text, self._fields_model.copy_key_at(index))
        return True

    def _set_hover(self, index: QModelIndex) -> None:
        if index != self._hover:
            self._hover = self._delegate.hover = QPersistentModelIndex(index)
            self.viewport().update()

    def _on_clicked(self, index: QModelIndex | QPersistentModelIndex) -> None:
        row = self._fields_model.row_at(index)
        if row is None:
            self.setExpanded(index, not self.isExpanded(index))
        elif row.event is not None:
            self.eventActivated.emit(row.event)

    def _remember_fold(self, index: QModelIndex, *, folded: bool) -> None:
        section = self._fields_model.section_at(index)
        if not self._syncing and section is not None:
            self._fold_state[section.name] = folded

    def _sync_folds(self) -> None:
        self._syncing = True
        try:
            for position, section in enumerate(self._fields_model.sections()):
                index = self._fields_model.section_index(position)
                folded = self._fold_state.get(section.name, section.folded_by_default)
                if self.isExpanded(index) == folded:
                    self.setExpanded(index, not folded)
        finally:
            self._syncing = False

    def _measure(self) -> tuple[int, int]:
        """Return the width of the longest label and of the widest value. Labels and event rows are measured again only
        when the rows or the fonts change, since an event never changes; field values whenever rows are set, since an
        object with the same fields has other values.
        """
        fonts = self._delegate.fonts
        if self._measured_for != self._fields_model.structure:
            rows = self._fields_model.rows()
            self._longest_label = max((row.label_px(fonts) for row in rows), default=0)
            self._widest_event = max((row.value_px(fonts) for row in rows if row.event is not None), default=0)
            self._measured_for = self._fields_model.structure
        if not self._values_measured:
            fields = max((row.value_px(fonts) for row in self._fields_model.rows() if row.event is None), default=0)
            self._widest_value = max(fields, self._widest_event)
            self._values_measured = True
        return self._longest_label, self._widest_value

    def _fit_labels(self) -> None:
        """Make the label column as wide as the longest label, but leave a long value such as an id its room where
        it can, and the label at least a third of the row; a cut label is cut in the middle.
        """
        fonts = self._delegate.fonts
        longest, widest = self._measure()
        inner = _row_area(QRect(0, 0, self.viewport().width(), 1)).width()
        value_room = min(widest, _ID_CHARS * fonts.mono_metrics.averageCharWidth())
        width = min(longest, max(inner // 3, inner - Space.M - value_room))
        if width != self._delegate.label_width:
            self._delegate.label_width = width
            self.viewport().update()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return a width that does not depend on the rows: some of every label, the start of a value and the scroll
        bar if there may be one, so the panel does not change width from one object to the next.
        """
        fonts = self._delegate.fonts
        around = -_row_area(QRect(0, 0, 0, 1)).width()  # What a row spends on the arrow and the copy column.
        bar = (
            0
            if self.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            else self.verticalScrollBar().sizeHint().width()
        )
        width = (
            _LABEL_CHARS * fonts.body_metrics.averageCharWidth()
            + Space.M
            + _VALUE_CHARS * fonts.mono_metrics.averageCharWidth()
            + around
            + bar
        )
        return QSize(width, super().minimumSizeHint().height())

    def _apply_metrics(self) -> None:
        self._measured_for = -1
        self._values_measured = False
        self._delegate.load_metrics()
        self._fit_labels()
        self.updateGeometry()
        self.doItemsLayout()
        self.viewport().update()
