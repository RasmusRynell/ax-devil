"""A foldable key/value tree for an object's fields and events, painted by one delegate with no widget per row.

``Section`` groups ``Row`` objects under a caption. ``FieldsModel`` holds the sections, so a frame's new values repaint
the rows in place while the structure stays; ``FieldsView`` remembers which sections the user folded. Rows without a
section (the object's id) come first. Every row ends in the copy column shared with the lists, and the label column is
as wide as the longest label of the whole view, so values line up across sections.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QItemSelectionModel,
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
from ax_devil.modules.chrome.chip import paint_tag
from ax_devil.modules.chrome.copy_cell import COPY_PX, COPY_TOOLTIP, CopiedMark, paint_copy_icon
from ax_devil.modules.chrome.list_row import paint_row_background
from ax_devil.modules.chrome.section_caption import paint_section_caption
from ax_devil.modules.chrome.theme import FLUSH_VIEW, category_color
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent
from ax_devil.modules.object_inspection.frame_labels import format_frame_time
from ax_devil.modules.scene.inspection import FieldGroup, InspectionField, entity_fields
from ax_devil.modules.scene.model import Entity

EVENTS_SECTION = "Events"

_ITEM_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_ARROW_PX = Space.M  # Rows and captions start here, so their text lines up with other content at Space.M plus this.
_VALUE_CHARS = 14  # The characters of a value, such as an id, that stay visible however narrow the view is.
_EVENT_TEXT_CHARS = (
    4  # An event's text is left out when it has less room than this, rather than shown as a lone ellipsis.
)
_BAR_CHARS = 10


@dataclass(frozen=True, slots=True)
class RowFonts:
    """The fonts and their metrics a row paints with, for the current text size."""

    body: QFont
    mono: QFont
    body_metrics: QFontMetrics
    mono_metrics: QFontMetrics

    @classmethod
    def current(cls) -> RowFonts:
        """Return the fonts for the current text size."""
        body, mono = TextRole.BODY.font(), TextRole.MONO.font()
        return cls(body, mono, QFontMetrics(body), QFontMetrics(mono))


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
    copying it puts on the clipboard. A row that is an event carries it in *event*; a row with a *label* has it
    drawn in the view's label column.
    """

    event: FrameEvent | None = None
    label: str = ""

    @property
    @abstractmethod
    def key(self) -> Hashable:
        """Return what identifies this row when the same section is shown again with new values."""

    @abstractmethod
    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts, label_width: int) -> None:
        """Draw the row inside *rect*, its label in the first *label_width* pixels."""

    @abstractmethod
    def copy_text(self) -> str:
        """Return the full text the copy icon and Ctrl+C put on the clipboard."""

    def label_px(self, fonts: RowFonts) -> int:
        """Return the width the row's label needs in the label column, 0 if it has none."""
        return 0

    def value_min_px(self, fonts: RowFonts) -> int:
        """Return the width the row's value keeps before its label gives way."""
        return 0

    def cut_text(self, rect: QRect, fonts: RowFonts, label_width: int) -> str | None:
        """Return the full text of the row if painting it in *rect* cuts anything off, else ``None``."""
        return None


def format_field_value(value: str | int | float | bool) -> str:
    """Return *value* as the tree shows it: booleans lowercase, floats to four significant digits."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.1f}" if abs(value) >= 1e4 else f"{value:.4g}"
    return str(value)


class FieldRow(Row):
    """A *label* in muted text and its *text* in monospace, with a thin bar for a score, or after a colored *tag*.

    Copying gives *copy*, the value in full (the text itself if not given); a long value is cut in the way *elide*
    says, but keeps at least *min_chars* characters visible after its tag.
    """

    def __init__(
        self,
        label: str,
        text: str,
        *,
        copy: str | None = None,
        fraction: float | None = None,
        tag: str | None = None,
        elide: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        min_chars: int = _VALUE_CHARS,
    ) -> None:
        self.label = label
        self._min_chars = min_chars
        self._text = text
        self._copy = text if copy is None else copy
        self._fraction = fraction
        self._tag = tag
        self._elide = elide

    @property
    def key(self) -> Hashable:
        """Return the label."""
        return self.label

    def _areas(self, rect: QRect, fonts: RowFonts, label_width: int) -> tuple[QRect, QRect, QRect | None]:
        """Return the label's area, the value's, and the score bar's groove if the value leaves room for one."""
        label = QRect(rect.left(), rect.top(), label_width, rect.height())
        left = rect.left() + label_width + Space.M
        value = QRect(left, rect.top(), max(0, rect.right() - left + 1), rect.height())
        bar_width = _BAR_CHARS * fonts.mono_metrics.averageCharWidth()
        if self._fraction is None or value.width() < 2 * bar_width:
            return label, value, None
        groove = QRect(value.right() - bar_width + 1, value.top() + (value.height() - Space.S) // 2, bar_width, Space.S)
        value.setRight(groove.left() - Space.M)
        return label, value, groove

    def label_px(self, fonts: RowFonts) -> int:
        """Return the width of the label."""
        return fonts.body_metrics.horizontalAdvance(self.label) + 1

    def value_min_px(self, fonts: RowFonts) -> int:
        """Return the width of the tag and *min_chars* characters."""
        return self._tag_width() + self._min_chars * fonts.mono_metrics.averageCharWidth()

    def _tag_width(self) -> int:
        """Return the width of the tag and the gap after it, or 0 without a tag."""
        if self._tag is None:
            return 0
        return QFontMetrics(TextRole.SMALL.font()).horizontalAdvance(self._tag) + 2 * Space.M + Space.M

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts, label_width: int) -> None:
        """Draw the label, then the value with its bar at the right edge when it has one."""
        label, value, groove = self._areas(rect, fonts, label_width)
        muted = palette.color(QPalette.ColorRole.PlaceholderText)
        _draw_text(painter, label, fonts.body, muted, self.label, Qt.TextElideMode.ElideMiddle)
        if groove is not None and self._fraction is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.color(QPalette.ColorRole.Mid))
            painter.drawRoundedRect(groove, Space.XS, Space.XS)
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
            filled = QRect(groove.left(), groove.top(), round(groove.width() * self._fraction), groove.height())
            painter.drawRoundedRect(filled, Space.XS, Space.XS)
        if self._tag is not None:
            value.setLeft(
                value.left() + paint_tag(painter, value, self._tag, category_color(self._tag, palette)) + Space.M
            )
        _draw_text(painter, value, fonts.mono, palette.color(QPalette.ColorRole.Text), self._text, self._elide)

    def cut_text(self, rect: QRect, fonts: RowFonts, label_width: int) -> str | None:
        """Return ``label: value`` in full if the label or the value is cut off."""
        label, value, _ = self._areas(rect, fonts, label_width)
        if (
            fonts.body_metrics.horizontalAdvance(self.label) > label.width()
            or fonts.mono_metrics.horizontalAdvance(self._text) + self._tag_width() > value.width()
        ):
            return f"{self.label}: {self._copy}"
        return None

    def copy_text(self) -> str:
        """Return the value in full."""
        return self._copy


def field_row(field: InspectionField) -> FieldRow:
    """Return *field* as a row; a float copies with all its digits."""
    value = field.value
    copy = str(value) if isinstance(value, float) else format_field_value(value)
    return FieldRow(".".join(field.path), format_field_value(value), copy=copy, fraction=field.fraction)


def id_row(entity_id: str) -> FieldRow:
    """Return the row that shows an object's *entity_id*, cut in the middle if it does not fit."""
    return FieldRow("id", entity_id, elide=Qt.TextElideMode.ElideMiddle)


class EventRow(Row):
    """An event involving the object, on the view's grid: its time in monospace as the label, then its kind as a
    colored tag and its label as the value.
    """

    def __init__(self, event: FrameEvent) -> None:
        self.event = event
        self.label = format_frame_time(event.frame_id.timestamp_monotime_us)

    @property
    def key(self) -> Hashable:
        """Return the event's frame, kind and label."""
        event = self.event
        assert event is not None
        return (event.frame_id.sequence_id, event.kind, event.label)

    def label_px(self, fonts: RowFonts) -> int:
        """Return the width of the time."""
        return fonts.mono_metrics.horizontalAdvance(self.label) + 1

    def value_min_px(self, fonts: RowFonts) -> int:
        """Return the width of the kind tag."""
        event = self.event
        assert event is not None
        return QFontMetrics(TextRole.SMALL.font()).horizontalAdvance(event.kind) + 2 * Space.M

    def _value_left(self, rect: QRect, label_width: int) -> int:
        return rect.left() + label_width + Space.M

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts, label_width: int) -> None:
        """Draw the time in the label column, then the kind tag and the label in the value column."""
        event = self.event
        assert event is not None
        _draw_text(
            painter,
            QRect(rect.left(), rect.top(), label_width, rect.height()),
            fonts.mono,
            palette.color(QPalette.ColorRole.PlaceholderText),
            self.label,
        )
        tag_left = self._value_left(rect, label_width)
        tag_width = paint_tag(
            painter,
            QRect(tag_left, rect.top(), rect.right() - tag_left + 1, rect.height()),
            event.kind,
            category_color(event.kind, palette),
        )
        text_left = tag_left + tag_width + Space.M
        if rect.right() - text_left >= _EVENT_TEXT_CHARS * fonts.body_metrics.averageCharWidth():
            _draw_text(
                painter,
                QRect(text_left, rect.top(), rect.right() - text_left + 1, rect.height()),
                fonts.body,
                palette.color(QPalette.ColorRole.Text),
                event.label,
            )

    def cut_text(self, rect: QRect, fonts: RowFonts, label_width: int) -> str | None:
        """Return the whole event if its time, kind or label is cut off."""
        event = self.event
        assert event is not None
        tag = QFontMetrics(TextRole.SMALL.font()).horizontalAdvance(event.kind) + 3 * Space.M
        shown = rect.right() + 1 - self._value_left(rect, label_width) - tag
        if (
            fonts.mono_metrics.horizontalAdvance(self.label) > label_width
            or fonts.body_metrics.horizontalAdvance(event.label) > shown
        ):
            return self.copy_text()
        return None

    def copy_text(self) -> str:
        """Return time, kind and label."""
        event = self.event
        assert event is not None
        return f"{self.label} {event.kind} {event.label}"


@dataclass(frozen=True, slots=True)
class Section:
    """A captioned, foldable run of rows; *name* is the caption and the key its fold state is remembered by.

    A section with *copy* has a copy icon in its caption that copies what *copy* returns.
    """

    name: str
    rows: tuple[Row, ...]
    folded_by_default: bool = False
    copy: Callable[[], str] | None = None


def field_sections(entity: Entity) -> tuple[Section, ...]:
    """Return the entity's populated field groups as sections, in display order; Raw copies as JSON."""
    return tuple(
        Section(
            group.title,
            tuple(field_row(item) for item in items),
            group.folded_by_default,
            (lambda items=items: _json_of(items)) if group is FieldGroup.RAW else None,
        )
        for group, items in entity_fields(entity)
    )


def _json_of(items: Sequence[InspectionField]) -> str:
    """Return *items* as a JSON object keyed by their dotted paths."""
    return json.dumps({".".join(item.path): item.value for item in items}, indent=2)


def event_section(events: Sequence[FrameEvent]) -> Section:
    """Return *events* as the Events section."""
    return Section(EVENTS_SECTION, tuple(EventRow(event) for event in events))


class FieldsModel(QAbstractItemModel):
    """Sections as top-level rows and their rows as children, in one column; the lead rows come first at the top."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lead: list[Row] = []
        self._sections: list[Section] = []
        self.structure = 0  # Changes whenever the set of rows does, so what is measured from them can be reused.

    def sections(self) -> tuple[Section, ...]:
        """Return the sections."""
        return tuple(self._sections)

    def section_index(self, position: int) -> QModelIndex:
        """Return the index of the caption of the section at *position*."""
        return self.index(position + len(self._lead), 0)

    def rows(self) -> list[Row]:
        """Return every row, the lead rows first."""
        return [*self._lead, *(row for section in self._sections for row in section.rows)]

    def set_sections(self, sections: Sequence[Section], lead: Sequence[Row] = ()) -> bool:
        """Show *sections* under the *lead* rows; return whether the model reset, which collapses every section in a
        view.

        The same sections with the same row keys only repaint. A section whose rows changed swaps just its own rows.
        """
        new = list(sections)
        if [section.name for section in new] != [section.name for section in self._sections] or [
            row.key for row in lead
        ] != [row.key for row in self._lead]:
            self.beginResetModel()
            self._lead, self._sections = list(lead), new
            self.structure += 1
            self.endResetModel()
            return True
        if self._lead:
            self._lead = list(lead)
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._lead) - 1, 0))
        for position, section in enumerate(new):
            self._update_section(position, section)
        return False

    def _update_section(self, position: int, section: Section) -> None:
        old = self._sections[position]
        parent = self.section_index(position)
        if [row.key for row in old.rows] == [row.key for row in section.rows]:
            self._sections[position] = section
            if section.rows:
                self.dataChanged.emit(self.index(0, 0, parent), self.index(len(section.rows) - 1, 0, parent))
            return
        self.structure += 1
        if old.rows:
            self.beginRemoveRows(parent, 0, len(old.rows) - 1)
            self._sections[position] = Section(old.name, (), old.folded_by_default, old.copy)
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
        if not index.isValid() or index.internalId() != 0 or index.row() < len(self._lead):
            return None
        return self._sections[index.row() - len(self._lead)]

    def row_at(self, index: QModelIndex | QPersistentModelIndex) -> Row | None:
        """Return the row at *index*, or ``None`` if *index* is not a row."""
        if not index.isValid():
            return None
        if index.internalId() == 0:
            return self._lead[index.row()] if index.row() < len(self._lead) else None
        return self._sections[index.internalId() - 1].rows[index.row()]

    def copy_text_at(self, index: QModelIndex | QPersistentModelIndex) -> str | None:
        """Return what copying the row or caption at *index* puts on the clipboard, or ``None`` if it has no copy."""
        row, section = self.row_at(index), self.section_at(index)
        if row is not None:
            return row.copy_text()
        return section.copy() if section is not None and section.copy is not None else None

    def copy_key_at(self, index: QModelIndex | QPersistentModelIndex) -> Hashable:
        """Return what tells the row or caption at *index* from the others, such as across the objects of a page."""
        row, section = self.row_at(index), self.section_at(index)
        if row is not None:
            parent = self.section_at(index.parent())
            return (parent.name if parent is not None else "", row.key)
        return (section.name, None) if section is not None else None

    def index(  # noqa: D102
        self, row: int, column: int, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> QModelIndex:
        if column != 0 or row < 0:
            return QModelIndex()
        if parent is None or not parent.isValid():
            return self.createIndex(row, 0, 0) if row < len(self._lead) + len(self._sections) else QModelIndex()
        section = parent.row() - len(self._lead)
        if parent.internalId() != 0 or section < 0 or row >= len(self._sections[section].rows):
            return QModelIndex()
        return self.createIndex(row, 0, section + 1)

    def parent(self, child: QModelIndex | QPersistentModelIndex | None = None) -> Any:  # noqa: D102
        if child is None:
            return super().parent()
        if not child.isValid() or child.internalId() == 0:
            return QModelIndex()
        return self.createIndex(child.internalId() - 1 + len(self._lead), 0, 0)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802, D102
        if parent is None or not parent.isValid():
            return len(self._lead) + len(self._sections)
        section = self.section_at(parent)
        return len(section.rows) if section is not None else 0

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802, D102
        return 1

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:  # noqa: D102
        flags = Qt.ItemFlag.ItemIsEnabled
        return flags | Qt.ItemFlag.ItemIsSelectable if self.row_at(index) is not None else flags

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:  # noqa: D102
        section, row = self.section_at(index), self.row_at(index)
        if role == _ITEM_ROLE:
            return section or row
        if role == int(Qt.ItemDataRole.DisplayRole):
            return section.name if section is not None else row.copy_text() if row is not None else None
        return None


def _row_area(rect: QRect) -> QRect:
    """Return the part of a row's *rect* its content gets: after the fold arrow's indent and before the copy column."""
    return rect.adjusted(_ARROW_PX, 0, -(COPY_PX + Space.S), 0)


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
        if self._fields.copy_text_at(index) is not None and event.pos().x() > option.rect.right() - COPY_PX:
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
        has_copy = self._fields.copy_text_at(index) is not None
        painter.save()
        painter.setClipRect(option.rect)
        body = option.rect.adjusted(0, 0, -COPY_PX if has_copy else 0, 0)
        if row is not None:
            row.paint(painter, _row_area(option.rect), palette, self.fonts, self.label_width)
        elif section is not None:
            self._paint_section(painter, option, body, palette, section, self._view.isExpanded(index))
        if has_copy:
            copied = self.copied is not None and self.copied.key == self._fields.copy_key_at(index)
            copy_rect = QRect(option.rect.right() - COPY_PX + 1, option.rect.top(), COPY_PX, option.rect.height())
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
        self._measured = (0, 0)
        self._measured_for = -1
        self._minimum_width = 0
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
            and position.x() >= self.viewport().width() - COPY_PX
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
        """Return the width of the longest label and the room the values keep, measured again only when the rows or
        the fonts change, not when a frame only brings new values.
        """
        if self._measured_for != self._fields_model.structure:
            fonts = self._delegate.fonts
            rows = self._fields_model.rows()
            self._measured = (
                max((row.label_px(fonts) for row in rows), default=0),
                max((row.value_min_px(fonts) for row in rows), default=0),
            )
            self._measured_for = self._fields_model.structure
        return self._measured

    def _fit_labels(self) -> None:
        """Make the label column as wide as the longest label, unless that leaves the values too little room; the
        view is never narrower than that, so no label and no value's minimum is cut.
        """
        longest, value_room = self._measure()
        inner = _row_area(QRect(0, 0, self.viewport().width(), 1)).width()
        width = min(longest, max(inner - Space.M - value_room, inner // 3))
        if width != self._delegate.label_width:
            self._delegate.label_width = width
            self.viewport().update()
        around = -_row_area(QRect(0, 0, 0, 1)).width()  # What a row spends on the arrow and the copy column.
        minimum = longest + Space.M + value_room + around
        if minimum != self._minimum_width:
            self._minimum_width = minimum
            self.updateGeometry()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the width that shows every label and the start of every value."""
        return QSize(self._minimum_width, super().minimumSizeHint().height())

    def _apply_metrics(self) -> None:
        self._measured_for = -1
        self._delegate.load_metrics()
        self._fit_labels()
        self.doItemsLayout()
        self.viewport().update()
