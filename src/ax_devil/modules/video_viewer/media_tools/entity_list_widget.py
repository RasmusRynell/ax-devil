"""Structured entity list widget for the side panel.

Shows either the entities of the displayed Scene or, with a `SceneHistory`, every object of the overlay source with
the ones on the displayed frame highlighted. The list scrolls itself so only visible rows are built.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from math import ceil
from typing import Any, ClassVar, Protocol, Sequence

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFontMetrics,
    QPainter,
    QPalette,
    QShowEvent,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QLabel,
    QListView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.data_sources.scene_history import ObjectHistory, SceneHistory
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.inspection import entity_detail_items
from ax_devil.modules.scene.model import Entity, MotionState, Scene
from ax_devil.modules.video_viewer.scene_inspection import SceneRefilter

from .frame_labels import CURRENT_FRAME_HIGHLIGHT, format_short_frame_time

_TYPE_COLORS: dict[str, str] = {
    "human": "#4e8ef7",
    "head": "#4e8ef7",
    "face": "#4e8ef7",
    "bag": "#4e8ef7",
    "animal": "#7aa36d",
    "vehicle": "#d89a52",
    "vehicle_other": "#d89a52",
    "car": "#d89a52",
    "bus": "#d89a52",
    "truck": "#d89a52",
    "bike": "#d89a52",
    "bicycle": "#d89a52",
    "license_plate": "#d89a52",
}
_DEFAULT_TYPE_COLOR = "#7f7f7f"
_ENTITY_ROLE = Qt.ItemDataRole.UserRole + 1
_DETAIL_ROLE = Qt.ItemDataRole.UserRole + 3
_EXPANDED_ROLE = Qt.ItemDataRole.UserRole + 4
_ROW_FALLBACK_WIDTH = 240
_DOT_DIAMETER = 8


@dataclass(frozen=True, slots=True)
class SummarySpan:
    """One painted piece of the always-visible row summary."""

    text: str
    color: StatusColor | None
    font: str


class EntityScope(enum.Enum):
    """Which entities the entity list shows."""

    FRAME = "frame"
    FILE = "file"

    @property
    def title(self) -> str:
        """Return the scope switch label."""
        return {EntityScope.FRAME: "Frame", EntityScope.FILE: "File"}[self]

    @property
    def tooltip(self) -> str:
        """Return the scope switch help."""
        return {
            EntityScope.FRAME: "Entities in the displayed frame",
            EntityScope.FILE: "Every object in the overlay; objects on the displayed frame are highlighted",
        }[self]


class EntityRow(Protocol):
    """One entity list row; each kind of row describes itself."""

    expandable: ClassVar[bool]

    @property
    def entity_id(self) -> str:
        """Return the row's entity id."""

    def types(self) -> tuple[str, ...]:
        """Return classification types shown as colored dots."""

    def trailing(self) -> tuple[SummarySpan, ...]:
        """Return the right-aligned summary columns."""

    def detail_html(self) -> str:
        """Return expanded detail HTML."""

    def is_visible_at(self, frame: int | None) -> bool:
        """Return whether the row's entity is on *frame*."""


@dataclass(frozen=True, slots=True)
class FrameEntityRow:
    """An entity of the displayed Scene; expands to its current details."""

    entity: Entity
    expandable: ClassVar[bool] = True

    @property
    def entity_id(self) -> str:
        """Return the row's entity id."""
        return str(self.entity.id)

    def types(self) -> tuple[str, ...]:
        """Return the latest observation's classification types."""
        obs = self.entity.latest_observation
        return tuple(c.type for c in obs.classification) if obs is not None else ()

    def trailing(self) -> tuple[SummarySpan, ...]:
        """Return movement and confidence columns."""
        return _build_trailing(self.entity)

    def detail_html(self) -> str:
        """Return every populated entity and latest-observation field."""
        return entity_detail_html(self.entity)

    def is_visible_at(self, frame: int | None) -> bool:
        """Frame rows are the displayed frame; they need no highlight."""
        return False


@dataclass(frozen=True, slots=True)
class FileObjectRow:
    """An object of the whole overlay source; its details live in the object history pane."""

    history: ObjectHistory
    expandable: ClassVar[bool] = False

    @property
    def entity_id(self) -> str:
        """Return the row's entity id."""
        return self.history.entity_id

    def types(self) -> tuple[str, ...]:
        """Return every classification type the object had."""
        return self.history.types

    def trailing(self) -> tuple[SummarySpan, ...]:
        """Return when the object is first and last shown."""
        first = format_short_frame_time(self.history.first_seen.timestamp_monotime_us)
        last = format_short_frame_time(self.history.last_seen.timestamp_monotime_us)
        return (SummarySpan(f"{first}–{last}", None, "value"),)

    def detail_html(self) -> str:
        """File rows do not expand."""
        return ""

    def is_visible_at(self, frame: int | None) -> bool:
        """Return whether the object is shown on *frame*."""
        return frame is not None and self.history.is_visible_at(frame)


@dataclass(frozen=True, slots=True)
class EntityListItem:
    """Presentation data for one entity list row."""

    entity_id: str
    types: tuple[str, ...]
    trailing: tuple[SummarySpan, ...]  # Right-aligned columns.
    detail_html: str  # Empty while collapsed.
    expanded: bool
    visible: bool  # On the displayed frame, for whole-file rows.


class EntityListModel(QAbstractListModel):
    """List model for entity rows; row presentation is built only when a view asks for it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[EntityRow] = []
        self._ids: list[str] = []
        self._expanded: set[str] = set()
        self._items: dict[int, EntityListItem] = {}  # Dropped on every scene.
        self._current_frame: int | None = None

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        """Return the number of entity rows."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)
    ) -> object | None:
        """Return row data for Qt view roles."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        role_value = int(role)
        if role_value == _EXPANDED_ROLE:
            return self._ids[index.row()] in self._expanded
        item = self.item_at(index.row())
        if item is None:
            return None
        if role_value == int(Qt.ItemDataRole.DisplayRole):
            return " ".join((*item.types, item.entity_id, *(span.text for span in item.trailing)))
        if role_value == int(Qt.ItemDataRole.ToolTipRole):
            return " · ".join((*item.types, item.entity_id))
        if role_value == _DETAIL_ROLE:
            return item.detail_html
        if role_value == _ENTITY_ROLE:
            return item
        return None

    def set_rows(self, rows: Sequence[EntityRow]) -> None:
        """Show *rows*, updating them in place when the ids are unchanged."""
        ids = [row.entity_id for row in rows]
        self._items.clear()
        if ids == self._ids:
            self._rows = list(rows)
            if self._expanded:
                # Expanded detail heights may change, which dataChanged does not relayout.
                self.layoutAboutToBeChanged.emit()
                self.layoutChanged.emit()
            elif ids:
                self.dataChanged.emit(self.index(0, 0), self.index(len(ids) - 1, 0))
            return
        self.beginResetModel()
        self._rows = list(rows)
        self._ids = ids
        self._expanded.intersection_update(row.entity_id for row in rows if row.expandable)
        self.endResetModel()

    def clear(self) -> None:
        """Remove all entity rows."""
        self.set_rows(())

    def set_current_frame(self, frame: int | None) -> bool:
        """Rebuild rows against the displayed frame when it changed; return whether it did.

        The highlight never changes row sizes, so views only need to repaint their visible rows. Announcing every row
        with ``dataChanged`` would make Qt walk the whole model on each frame.
        """
        if frame == self._current_frame:
            return False
        self._current_frame = frame
        self._items.clear()
        return True

    def item_at(self, row: int) -> EntityListItem | None:
        """Return the item at *row*, if it exists."""
        if not 0 <= row < len(self._rows):
            return None
        item = self._items.get(row)
        if item is None:
            entity_row = self._rows[row]
            expanded = entity_row.expandable and self._ids[row] in self._expanded
            item = EntityListItem(
                entity_id=self._ids[row],
                types=entity_row.types(),
                trailing=entity_row.trailing(),
                detail_html=entity_row.detail_html() if expanded else "",
                expanded=expanded,
                visible=entity_row.is_visible_at(self._current_frame),
            )
            self._items[row] = item
        return item

    def has_expanded(self) -> bool:
        """Return whether any current row is expanded."""
        return bool(self._expanded)

    def set_expanded(self, row: int, expanded: bool) -> None:
        """Set expansion state for one expandable row."""
        if not 0 <= row < len(self._ids) or (self._ids[row] in self._expanded) == expanded:
            return
        if not self._rows[row].expandable:
            return
        if expanded:
            self._expanded.add(self._ids[row])
        else:
            self._expanded.discard(self._ids[row])
        self._items.pop(row, None)
        model_index = self.index(row, 0)
        self.dataChanged.emit(model_index, model_index, [_EXPANDED_ROLE, _DETAIL_ROLE])


class EntityListDelegate(QStyledItemDelegate):
    """Paint entity list rows without creating child widgets."""

    def __init__(self, view: QAbstractItemView) -> None:
        super().__init__(view)
        self._view = view
        follow_appearance(self, self._load_fonts)

    def _load_fonts(self) -> None:
        self._fonts = {"id": TextRole.MONO.font(), "value": TextRole.MONO.font(), "glyph": TextRole.BODY.font()}
        self._metrics = {name: QFontMetrics(font) for name, font in self._fonts.items()}
        # Fixed-width confidence column so values line up across rows.
        self._min_widths = {"value": self._metrics["value"].horizontalAdvance("100%")}
        self._summary_height = max(metrics.height() for metrics in self._metrics.values())
        self._view.doItemsLayout()

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Paint one entity row."""
        painter.save()
        painter.setClipRect(option.rect)
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style = style_option.widget.style() if style_option.widget is not None else QApplication.style()
        rect = option.rect
        content_width = self._content_width(option)
        content_left = rect.left() + Space.S
        content_top = rect.top() + Space.XS
        item = index.data(_ENTITY_ROLE)
        if isinstance(item, EntityListItem):
            if item.expanded:
                painter.fillRect(rect, option.palette.brush(QPalette.ColorRole.AlternateBase))
            if item.visible:
                painter.fillRect(rect, CURRENT_FRAME_HIGHLIGHT)
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, style_option, painter, style_option.widget)
        if isinstance(item, EntityListItem):
            palette = QPalette(option.palette)
            if option.state & QStyle.StateFlag.State_Selected:
                palette.setColor(QPalette.ColorRole.Text, palette.color(QPalette.ColorRole.HighlightedText))
            self._draw_summary(painter, content_left, content_left + content_width, content_top, item, palette)
            if item.expanded:
                detail_top = content_top + self._summary_height + Space.XS
                detail_rect = QRect(
                    content_left, detail_top, content_width, self._html_height(item.detail_html, content_width)
                )
                self._draw_html(painter, detail_rect, item.detail_html, palette)
        painter.setPen(option.palette.color(QPalette.ColorRole.Mid))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        """Return a fixed collapsed height, plus laid-out detail height when expanded."""
        height = (Space.XS * 2) + self._summary_height
        if bool(index.data(_EXPANDED_ROLE)):
            content_width = self._content_width(option)
            detail_height = self._html_height(str(index.data(_DETAIL_ROLE) or ""), content_width)
            height += Space.XS + detail_height + Space.XS
        return QSize(option.rect.width(), height)

    def _draw_summary(
        self, painter: QPainter, left: int, right: int, top: int, item: EntityListItem, palette: QPalette
    ) -> None:
        align_right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        x = right
        for span in reversed(item.trailing):
            width = max(self._metrics[span.font].horizontalAdvance(span.text), self._min_widths.get(span.font, 0))
            self._draw_span(
                painter, QRect(x - width, top, width, self._summary_height), span, span.text, align_right, palette
            )
            x -= width + Space.S
        limit = x
        # Untyped (motion) entities keep a gray dot so ids stay in one column.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        dot_top = top + (self._summary_height - _DOT_DIAMETER) // 2
        x = left
        for object_type in item.types or ("",):
            painter.setBrush(QColor(type_color(object_type)))
            painter.drawEllipse(x, dot_top, _DOT_DIAMETER, _DOT_DIAMETER)
            x += _DOT_DIAMETER + Space.XS
        x += Space.S - Space.XS
        id_span = SummarySpan(item.entity_id, None, "id")
        text = self._metrics["id"].elidedText(item.entity_id, Qt.TextElideMode.ElideMiddle, max(0, limit - x))
        alignment = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._draw_span(
            painter, QRect(x, top, max(0, limit - x), self._summary_height), id_span, text, alignment, palette
        )

    def _draw_span(
        self, painter: QPainter, rect: QRect, span: SummarySpan, text: str, alignment: int, palette: QPalette
    ) -> None:
        painter.setFont(self._fonts[span.font])
        painter.setPen(span.color.color(palette) if span.color is not None else palette.color(QPalette.ColorRole.Text))
        painter.drawText(rect, alignment, text)

    @staticmethod
    def _draw_html(painter: QPainter, rect: QRect, html: str, palette: QPalette) -> None:
        painter.save()
        painter.translate(rect.topLeft())
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette = palette
        _text_document(html, rect.width(), TextRole.SMALL.px).documentLayout().draw(painter, context)
        painter.restore()

    def _content_width(self, option: QStyleOptionViewItem) -> int:
        # sizeHint gets no row rect; rows paint into the viewport, which excludes the scrollbar.
        row_width = option.rect.width() or self._view.viewport().width()
        if row_width <= 0:
            row_width = _ROW_FALLBACK_WIDTH
        return max(1, row_width - (Space.S * 2))

    @staticmethod
    def _html_height(html: str, width: int) -> int:
        return ceil(_text_document(html, width, TextRole.SMALL.px).size().height())


@lru_cache(maxsize=512)
def _text_document(html: str, width: int, size_px: int) -> QTextDocument:
    """Return a laid-out document at *size_px* text; shared by sizeHint and paint so each row is laid out once."""
    document = QTextDocument()
    font = TextRole.SMALL.font()
    font.setPixelSize(size_px)
    document.setDefaultFont(font)
    document.setHtml(html)
    document.setTextWidth(max(1, width))
    return document


def _build_trailing(entity: Entity) -> tuple[SummarySpan, ...]:
    """Build the right-aligned summary columns: movement, then confidence."""
    obs = entity.latest_observation
    spans: list[SummarySpan] = []
    if entity.motion_state is not None:
        spans.append(_movement_span(entity.motion_state))
    if obs is not None and obs.confidence is not None:
        conf = obs.confidence.value
        spans.append(SummarySpan(f"{conf:.0%}", _confidence_color(conf), "value"))
    return tuple(spans)


def _detail_row(key: str, value_html: str) -> str:
    """Return one key/value row of the expanded detail table."""
    key_style = f"font-weight:600; padding-right:{Space.M}px;"
    return f'<tr><td valign="top" style="{key_style}">{escape(key)}</td><td>{value_html}</td></tr>'


def entity_detail_html(entity: Entity) -> str:
    """Build detail HTML: every populated entity and latest-observation field, debug included."""
    rows = [_detail_row("id", f'<span style="font-family:monospace;">{escape(str(entity.id))}</span>')]
    rows.extend(_detail_row(name, html) for name, html in entity_detail_items(entity))
    return f'<table cellspacing="0" cellpadding="1">{"".join(rows)}</table>'


class EntityListWidget(QWidget):
    """Entity list for the side panel.

    Satisfies the ``SceneInspectorSink`` protocol. With a history it can also list every object of the overlay source.
    """

    entitySelected = Signal(str)  # entity id

    def __init__(self, parent: QWidget | None = None, *, show_title: bool = True) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(Space.M)

        # Header
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(Space.XS)

        if show_title:
            title = QLabel("Entities", self)
            TextRole.STRONG.apply(title)
            header_layout.addWidget(title)

        self._count_label = QLabel("", self)
        self._count_label.setStyleSheet("color: palette(placeholder-text);")
        header_layout.addWidget(self._count_label)

        root_layout.addLayout(header_layout)

        self._model = EntityListModel(self)
        self._list_view = QListView(self)
        self._list_view.setModel(self._model)
        self._list_view.setItemDelegate(EntityListDelegate(self._list_view))
        self._list_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._list_view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list_view.setFrameShape(QFrame.Shape.NoFrame)
        self._list_view.setUniformItemSizes(True)
        self._list_view.setSpacing(0)
        self._list_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self._list_view.clicked.connect(self._on_index_clicked)
        root_layout.addWidget(self._list_view)

        self._scene: Scene | None = None
        self._frame_id: FrameIdentifier | None = None
        self._refilter: SceneRefilter | None = None
        self._stale = False
        self._row_scene: Scene | None = None
        self._scope = EntityScope.FRAME
        self._history: SceneHistory | None = None
        self._scene_filter: SessionFilter | None = None
        self._file_rows_stale = True
        self.clear()

    def set_history(self, history: SceneHistory, scene_filter: SessionFilter) -> None:
        """Enable the whole-file scope, filtered like the overlay."""
        self._history = history
        self._scene_filter = scene_filter
        self._file_rows_stale = True
        if self._scope is EntityScope.FILE:
            self._show_latest()

    def set_scope(self, scope: EntityScope) -> None:
        """Show the displayed frame's entities or every object of the history."""
        if scope is self._scope:
            return
        self._scope = scope
        self._row_scene = None
        self._file_rows_stale = True
        self._model.set_current_frame(None)
        self._show_latest()

    # ------------------------------------------------------------------
    # SceneInspectorSink protocol
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear current contents."""
        self._scene = None
        self._frame_id = None
        self._refilter = None
        self._stale = False
        self._row_scene = None
        self._model.clear()
        self._count_label.setText("No scene data")

    def update_scene(
        self,
        scene: Scene | None,
        frame_id: FrameIdentifier | None,
        metadata: dict[str, Any] | None,
        refilter: SceneRefilter | None = None,
    ) -> None:
        """Update the entity list from a new scene."""
        self._scene = scene
        self._frame_id = frame_id
        self._refilter = refilter
        self._show_latest()

    def refilter(self) -> None:
        """Re-apply changed filters to the shown rows, so the list stays current while paused."""
        self._file_rows_stale = True
        if self._refilter is not None:
            self._scene = self._refilter()
        self._show_latest()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Materialize only the latest sample when the inspector opens."""
        super().showEvent(event)
        if self._stale:
            self._apply_scene()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _show_latest(self) -> None:
        """Apply the latest sample now, or defer it until the inspector is shown."""
        if self.isVisible():
            self._apply_scene()
        else:
            self._stale = True

    def _apply_scene(self) -> None:
        """Update rows for the current scope."""
        self._stale = False
        if self._scope is EntityScope.FILE:
            self._apply_file_objects()
        else:
            self._apply_frame_entities()

    def _apply_file_objects(self) -> None:
        """Show every filtered object of the history, highlighting those on the displayed frame."""
        assert self._history is not None and self._scene_filter is not None
        if self._file_rows_stale:
            self._file_rows_stale = False
            objects = self._scene_filter.filter_history(self._history.objects)
            self._model.set_rows([FileObjectRow(history) for history in objects])
            self._sync_uniform_item_sizes()
        if self._model.set_current_frame(self._frame_id.sequence_id if self._frame_id is not None else None):
            self._list_view.viewport().update()
        count = self._model.rowCount()
        self._count_label.setText(f"{count} object{'' if count == 1 else 's'} in the file" if count else "No objects")

    def _apply_frame_entities(self) -> None:
        """Update entity rows from the latest sample."""
        scene, frame_id = self._scene, self._frame_id
        if scene is None or not scene.entities:
            self._row_scene = None
            self._model.clear()
            self._count_label.setText("No entities")
            return

        entity_count = len(scene.entities)
        frame_text = f" — frame {frame_id.sequence_id}" if frame_id is not None else ""
        self._count_label.setText(f"{entity_count} entit{'y' if entity_count == 1 else 'ies'}{frame_text}")

        if scene is self._row_scene:
            return
        self._row_scene = scene
        self._model.set_rows([FrameEntityRow(entity) for entity in scene.entities.values()])
        self._sync_uniform_item_sizes()

    def _sync_uniform_item_sizes(self) -> None:
        """Let Qt skip per-row size hints while every row has the same collapsed height."""
        self._list_view.setUniformItemSizes(not self._model.has_expanded())

    def _on_index_clicked(self, index: QModelIndex) -> None:
        """Select the clicked row's entity and toggle its detail expansion when it has one."""
        item = self._model.item_at(index.row())
        if item is None:
            return
        self._model.set_expanded(index.row(), not item.expanded)
        self._sync_uniform_item_sizes()
        self._list_view.doItemsLayout()
        self._list_view.viewport().update()
        self.entitySelected.emit(item.entity_id)


# ------------------------------------------------------------------
# Shared presentation helpers
# ------------------------------------------------------------------


def type_color(object_type: str) -> str:
    """Return the accent color for an object type."""
    return _TYPE_COLORS.get(object_type.lower(), _DEFAULT_TYPE_COLOR)


def _confidence_color(confidence: float) -> StatusColor:
    """Return the status color for a confidence value."""
    conf = max(0.0, min(1.0, confidence))
    if conf < 0.4:
        return StatusColor.ERROR
    if conf < 0.7:
        return StatusColor.WARNING
    return StatusColor.SUCCESS


def _movement_span(motion_state: MotionState) -> SummarySpan:
    """Return a compact movement indicator."""
    if motion_state is MotionState.Moving:
        return SummarySpan("●", StatusColor.SUCCESS, "glyph")
    if motion_state is MotionState.Stationary:
        return SummarySpan("○", StatusColor.WARNING, "glyph")
    return SummarySpan("?", None, "glyph")
