"""A foldable key/value tree for an object's fields and events, painted by one delegate with no widget per row.

``Section`` groups ``Row`` objects under a caption. ``FieldsModel`` holds the sections, so a frame's new values repaint
the rows in place while the structure stays; ``FieldsView`` remembers which sections the user folded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
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
    QTreeView,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chip import paint_tag
from ax_devil.modules.chrome.list_row import paint_row_background
from ax_devil.modules.chrome.section_caption import paint_section_caption
from ax_devil.modules.chrome.theme import category_color
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent
from ax_devil.modules.object_inspection.frame_labels import format_frame_time
from ax_devil.modules.scene.inspection import InspectionField, entity_fields
from ax_devil.modules.scene.model import Entity

EVENTS_SECTION = "Events"

_ITEM_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_ARROW_PX = Space.L
_KEY_SHARE_PERCENT = 45
_BAR_CHARS = 10
_TIME_CHARS = len("00:00:00.000")


@dataclass(frozen=True, slots=True)
class RowFonts:
    """The fonts and their metrics a row paints with, for the current text size."""

    body: QFont
    mono: QFont
    mono_metrics: QFontMetrics

    @classmethod
    def current(cls) -> RowFonts:
        """Return the fonts for the current text size."""
        mono = TextRole.MONO.font()
        return cls(TextRole.BODY.font(), mono, QFontMetrics(mono))


def _draw_text(
    painter: QPainter,
    rect: QRect,
    font: QFont,
    color: QColor,
    text: str,
    elide: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
) -> None:
    painter.setFont(font)
    painter.setPen(color)
    shown = QFontMetrics(font).elidedText(text, elide, rect.width())
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), shown)


class Row(ABC):
    """One line under a section: it has a *key* that identifies it across updates, paints itself and says what
    copying it puts on the clipboard. A row that is an event carries it in *event*."""

    event: FrameEvent | None = None

    @property
    @abstractmethod
    def key(self) -> Hashable:
        """Return what identifies this row when the same section is shown again with new values."""

    @abstractmethod
    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts) -> None:
        """Draw the row inside *rect*."""

    @abstractmethod
    def copy_text(self) -> str:
        """Return the text Ctrl+C copies."""


def format_field_value(value: str | int | float | bool) -> str:
    """Return *value* as the tree shows it: booleans lowercase, floats to four significant digits."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.1f}" if abs(value) >= 1e4 else f"{value:.4g}"
    return str(value)


class FieldRow(Row):
    """An entity field: its dotted path in muted text, its value in monospace, and a thin bar for a score."""

    def __init__(self, field: InspectionField) -> None:
        self._key = ".".join(field.path)
        self._text = format_field_value(field.value)
        self._fraction = field.fraction

    @property
    def key(self) -> Hashable:
        """Return the dotted path."""
        return self._key

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts) -> None:
        """Draw the key, then the value with its bar at the right edge when it has one."""
        key_width = rect.width() * _KEY_SHARE_PERCENT // 100
        key_rect = QRect(rect.left(), rect.top(), key_width, rect.height())
        value_rect = QRect(
            rect.left() + key_width + Space.M, rect.top(), rect.width() - key_width - Space.M, rect.height()
        )
        _draw_text(
            painter,
            key_rect,
            fonts.body,
            palette.color(QPalette.ColorRole.PlaceholderText),
            self._key,
            Qt.TextElideMode.ElideMiddle,
        )
        bar_width = _BAR_CHARS * fonts.mono_metrics.averageCharWidth()
        if self._fraction is not None and value_rect.width() >= 2 * bar_width:
            groove = QRect(
                value_rect.right() - bar_width + 1,
                value_rect.top() + (value_rect.height() - Space.S) // 2,
                bar_width,
                Space.S,
            )
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.color(QPalette.ColorRole.Mid))
            painter.drawRoundedRect(groove, Space.XS, Space.XS)
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
            filled = QRect(groove.left(), groove.top(), round(bar_width * self._fraction), groove.height())
            painter.drawRoundedRect(filled, Space.XS, Space.XS)
            value_rect.setRight(groove.left() - Space.M)
        _draw_text(painter, value_rect, fonts.mono, palette.color(QPalette.ColorRole.Text), self._text)

    def copy_text(self) -> str:
        """Return ``key: value``."""
        return f"{self._key}: {self._text}"


class EventRow(Row):
    """An event involving the object: its time in monospace, its kind as a colored tag and its label."""

    def __init__(self, event: FrameEvent) -> None:
        self.event = event
        self._time = format_frame_time(event.frame_id.timestamp_monotime_us)

    @property
    def key(self) -> Hashable:
        """Return the event's frame, kind and label."""
        event = self.event
        assert event is not None
        return (event.frame_id.sequence_id, event.kind, event.label)

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts) -> None:
        """Draw time, kind tag and label from left to right."""
        event = self.event
        assert event is not None
        time_width = fonts.mono_metrics.horizontalAdvance("0" * _TIME_CHARS) + Space.S
        _draw_text(
            painter,
            QRect(rect.left(), rect.top(), time_width, rect.height()),
            fonts.mono,
            palette.color(QPalette.ColorRole.PlaceholderText),
            self._time,
        )
        tag_left = rect.left() + time_width + Space.M
        tag_width = paint_tag(
            painter,
            QRect(tag_left, rect.top(), rect.right() - tag_left + 1, rect.height()),
            event.kind,
            category_color(event.kind, palette),
        )
        label_left = tag_left + tag_width + Space.M
        if label_left < rect.right():
            _draw_text(
                painter,
                QRect(label_left, rect.top(), rect.right() - label_left + 1, rect.height()),
                fonts.body,
                palette.color(QPalette.ColorRole.Text),
                event.label,
            )

    def copy_text(self) -> str:
        """Return time, kind and label."""
        event = self.event
        assert event is not None
        return f"{self._time} {event.kind} {event.label}"


@dataclass(frozen=True, slots=True)
class Section:
    """A captioned, foldable run of rows; *name* is the caption and the key its fold state is remembered by."""

    name: str
    rows: tuple[Row, ...]
    folded_by_default: bool = False


def field_sections(entity: Entity) -> tuple[Section, ...]:
    """Return the entity's populated field groups as sections, in display order."""
    return tuple(
        Section(group.title, tuple(FieldRow(item) for item in items), group.folded_by_default)
        for group, items in entity_fields(entity)
    )


def event_section(events: Sequence[FrameEvent]) -> Section:
    """Return *events* as the Events section."""
    return Section(EVENTS_SECTION, tuple(EventRow(event) for event in events))


class FieldsModel(QAbstractItemModel):
    """Sections as top-level rows and their rows as children, in one column."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sections: list[Section] = []

    def sections(self) -> tuple[Section, ...]:
        """Return the sections."""
        return tuple(self._sections)

    def set_sections(self, sections: Sequence[Section]) -> bool:
        """Show *sections*; return whether the model reset, which collapses every section in a view.

        The same sections with the same row keys only repaint. A section whose rows changed swaps just its own rows.
        """
        new = list(sections)
        if [section.name for section in new] != [section.name for section in self._sections]:
            self.beginResetModel()
            self._sections = new
            self.endResetModel()
            return True
        for position, section in enumerate(new):
            self._update_section(position, section)
        return False

    def _update_section(self, position: int, section: Section) -> None:
        old = self._sections[position]
        parent = self.index(position, 0)
        if [row.key for row in old.rows] == [row.key for row in section.rows]:
            self._sections[position] = section
            if section.rows:
                self.dataChanged.emit(self.index(0, 0, parent), self.index(len(section.rows) - 1, 0, parent))
            return
        if old.rows:
            self.beginRemoveRows(parent, 0, len(old.rows) - 1)
            self._sections[position] = Section(old.name, (), old.folded_by_default)
            self.endRemoveRows()
        if section.rows:
            self.beginInsertRows(parent, 0, len(section.rows) - 1)
            self._sections[position] = section
            self.endInsertRows()
        else:
            self._sections[position] = section
        self.dataChanged.emit(parent, parent)

    def section_at(self, index: QModelIndex | QPersistentModelIndex) -> Section | None:
        """Return the section whose caption is at *index*, or ``None`` if *index* is not a caption."""
        return self._sections[index.row()] if index.isValid() and index.internalId() == 0 else None

    def row_at(self, index: QModelIndex | QPersistentModelIndex) -> Row | None:
        """Return the row at *index*, or ``None`` if *index* is not a row."""
        if not index.isValid() or index.internalId() == 0:
            return None
        return self._sections[index.internalId() - 1].rows[index.row()]

    def index(  # noqa: D102
        self, row: int, column: int, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> QModelIndex:
        if column != 0 or row < 0:
            return QModelIndex()
        if parent is None or not parent.isValid():
            return self.createIndex(row, 0, 0) if row < len(self._sections) else QModelIndex()
        if parent.internalId() != 0 or row >= len(self._sections[parent.row()].rows):
            return QModelIndex()
        return self.createIndex(row, 0, parent.row() + 1)

    def parent(self, child: QModelIndex | QPersistentModelIndex | None = None) -> Any:  # noqa: D102
        if child is None:
            return super().parent()
        if not child.isValid() or child.internalId() == 0:
            return QModelIndex()
        return self.createIndex(child.internalId() - 1, 0, 0)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802, D102
        if parent is None or not parent.isValid():
            return len(self._sections)
        return len(self._sections[parent.row()].rows) if parent.internalId() == 0 else 0

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802, D102
        return 1

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:  # noqa: D102
        flags = Qt.ItemFlag.ItemIsEnabled
        return flags | Qt.ItemFlag.ItemIsSelectable if index.isValid() and index.internalId() != 0 else flags

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:  # noqa: D102
        section, row = self.section_at(index), self.row_at(index)
        if role == _ITEM_ROLE:
            return section or row
        if role == int(Qt.ItemDataRole.DisplayRole):
            return section.name if section is not None else row.copy_text() if row is not None else None
        return None


class _Delegate(QStyledItemDelegate):
    """Paints section captions and asks each row to paint itself."""

    def __init__(self, view: QTreeView) -> None:
        super().__init__(view)
        self._view = view
        self.load_metrics()

    def load_metrics(self) -> None:
        """Read the fonts and row height for the current text size."""
        self._fonts = RowFonts.current()
        self._height = Height.ROW.px

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        """Return the uniform row height."""
        return QSize(option.rect.width(), self._height)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Paint a section's caption with its fold arrow, or a row inside the caption's indent."""
        item = index.data(_ITEM_ROLE)
        palette = QGuiApplication.palette()  # The view's own palette has the theme's selection colors.
        painter.save()
        painter.setClipRect(option.rect)
        if index.parent().isValid():
            item.paint(painter, option.rect.adjusted(_ARROW_PX, 0, -Space.M, 0), palette, self._fonts)
        else:
            self._paint_section(painter, option, palette, item, self._view.isExpanded(index))
        painter.restore()

    def _paint_section(
        self, painter: QPainter, option: QStyleOptionViewItem, palette: QPalette, section: Section, expanded: bool
    ) -> None:
        rect = option.rect
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
    objects fold alike. Sections start as their ``folded_by_default`` says until the user folds one. Ctrl+C copies
    the selected row, and a click on an event row emits ``eventActivated``.
    """

    eventActivated = Signal(object)  # FrameEvent

    def __init__(self, fold_state: dict[str, bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fold_state = fold_state
        self._syncing = False
        self._hover = QPersistentModelIndex()
        self._fields_model = FieldsModel(self)
        self._delegate = _Delegate(self)
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
        self.setStyleSheet("QTreeView, QTreeView::item { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.clicked.connect(self._on_clicked)
        self.expanded.connect(lambda index: self._remember_fold(index, folded=False))
        self.collapsed.connect(lambda index: self._remember_fold(index, folded=True))
        follow_appearance(self, self._apply_metrics)

    def set_sections(self, sections: Sequence[Section]) -> None:
        """Show *sections*, keeping the scroll position and the sections' fold state."""
        scroll = self.verticalScrollBar().value()
        was_reset = self._fields_model.set_sections(sections)
        self._sync_folds()
        if was_reset:
            self.doItemsLayout()
            self.verticalScrollBar().setValue(scroll)

    def selected_text(self) -> str | None:
        """Return the copy text of the selected row, or ``None`` if no row is selected."""
        for index in self.selectionModel().selectedIndexes():
            row = self._fields_model.row_at(index)
            if row is not None:
                return row.copy_text()
        return None

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
        """Copy the selected row on Ctrl+C; fold a section or open an event on Enter and Space."""
        if event.matches(QKeySequence.StandardKey.Copy):
            text = self.selected_text()
            if text is not None:
                QGuiApplication.clipboard().setText(text)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.currentIndex().isValid():
            self._on_clicked(self.currentIndex())
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Follow the row under the pointer."""
        self._set_hover(self.indexAt(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Clear the hovered row."""
        self._set_hover(QModelIndex())
        super().leaveEvent(event)

    def _set_hover(self, index: QModelIndex) -> None:
        if index != self._hover:
            self._hover = QPersistentModelIndex(index)
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
                index = self._fields_model.index(position, 0)
                folded = self._fold_state.get(section.name, section.folded_by_default)
                if self.isExpanded(index) == folded:
                    self.setExpanded(index, not folded)
        finally:
            self._syncing = False

    def _apply_metrics(self) -> None:
        self._delegate.load_metrics()
        self.doItemsLayout()
        self.viewport().update()
