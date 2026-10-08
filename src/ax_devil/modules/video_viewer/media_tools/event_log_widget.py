"""Scene event log for the side panel.

Lists Scene events such as deletes and renames on the video frame where each takes effect. With a `SceneHistory`
the log holds every event of the overlay source; without one (live) it keeps the newest events as they are shown.
Both follow the displayed frame through Scene inspection updates.
"""

from __future__ import annotations

import enum
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.video_viewer.scene_inspection import SceneRefilter

from .frame_labels import CURRENT_FRAME_HIGHLIGHT, format_frame_time

LIVE_EVENT_LIMIT = 1000
"""Live logs keep only this many of the newest events."""

_EVENT_ROLE = Qt.ItemDataRole.UserRole + 1
_STATE_ROLE = Qt.ItemDataRole.UserRole + 2


class EventRowState(enum.Enum):
    """Where an event row lies relative to the displayed frame."""

    PAST = "past"
    CURRENT = "current"
    UPCOMING = "upcoming"

    @property
    def text_role(self) -> QPalette.ColorRole:
        """Return the text palette role for rows in this state."""
        return QPalette.ColorRole.PlaceholderText if self is EventRowState.UPCOMING else QPalette.ColorRole.Text

    @property
    def background(self) -> QColor | None:
        """Return the row highlight for this state, if any."""
        return CURRENT_FRAME_HIGHLIGHT if self is EventRowState.CURRENT else None


@dataclass(frozen=True, slots=True)
class EventFilter:
    """Which events the log shows: a case-insensitive search of their labels, and the kinds to hide."""

    query: str = ""
    hidden_kinds: frozenset[str] = frozenset()

    def accepts(self, event: FrameEvent) -> bool:
        """Return whether *event* is shown."""
        return event.kind not in self.hidden_kinds and self.query.casefold() in event.label.casefold()


class EventLogModel(QAbstractListModel):
    """Frame-ordered rows of the events the filter accepts, and their state relative to the displayed frame."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all: list[FrameEvent] = []
        self._filter = EventFilter()
        self._events: list[FrameEvent] = []  # Accepted by the filter.
        self._frames: list[int] = []
        self._current_frame: int | None = None

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        """Return the number of event rows."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._events)

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)
    ) -> object | None:
        """Return row data for Qt view roles."""
        if not index.isValid() or not 0 <= index.row() < len(self._events):
            return None
        event = self._events[index.row()]
        role_value = int(role)
        if role_value == int(Qt.ItemDataRole.DisplayRole):
            frame_id = event.frame_id
            return f"#{frame_id.sequence_id} {format_frame_time(frame_id.timestamp_monotime_us)} {event.label}"
        if role_value == _EVENT_ROLE:
            return event
        if role_value == _STATE_ROLE:
            return self._state(event.frame_id.sequence_id)
        return None

    @property
    def total_count(self) -> int:
        """Return how many events the log holds, shown or not."""
        return len(self._all)

    def set_events(self, events: Iterable[FrameEvent]) -> None:
        """Replace all events with *events* in frame order."""
        self._all = sorted(events, key=lambda event: event.frame_id.sequence_id)
        self._show_accepted()

    def set_filter(self, event_filter: EventFilter) -> None:
        """Show only the events *event_filter* accepts."""
        self._filter = event_filter
        self._show_accepted()

    def append_events(self, events: Sequence[FrameEvent], *, limit: int) -> None:
        """Append newer *events*, dropping the oldest events beyond *limit*."""
        self._all.extend(events)
        accepted = [event for event in events if self._filter.accepts(event)]
        if accepted:
            first = len(self._events)
            self.beginInsertRows(QModelIndex(), first, first + len(accepted) - 1)
            self._events.extend(accepted)
            self._frames.extend(event.frame_id.sequence_id for event in accepted)
            self.endInsertRows()
        excess = len(self._all) - limit
        if excess <= 0:
            return
        # Rows keep the order of all events, so dropped rows are the first rows.
        dropped_rows = sum(1 for event in self._all[:excess] if self._filter.accepts(event))
        del self._all[:excess]
        if dropped_rows:
            self.beginRemoveRows(QModelIndex(), 0, dropped_rows - 1)
            del self._events[:dropped_rows]
            del self._frames[:dropped_rows]
            self.endRemoveRows()

    def kinds(self) -> list[str]:
        """Return the kinds of all events, sorted."""
        return sorted({event.kind for event in self._all})

    def _show_accepted(self) -> None:
        self.beginResetModel()
        self._events = [event for event in self._all if self._filter.accepts(event)]
        self._frames = [event.frame_id.sequence_id for event in self._events]
        self.endResetModel()

    def set_current_frame(self, frame: int) -> None:
        """Restate rows whose position relative to the displayed frame changed."""
        previous = self._current_frame
        if frame == previous:
            return
        self._current_frame = frame
        if previous is None:
            first, last = 0, len(self._frames) - 1
        else:
            first = bisect_left(self._frames, min(frame, previous))
            last = bisect_right(self._frames, max(frame, previous)) - 1
        if first <= last:
            self.dataChanged.emit(self.index(first, 0), self.index(last, 0), [_STATE_ROLE])

    def position_row(self) -> int | None:
        """Return the last row at or before the displayed frame, if any."""
        if self._current_frame is None:
            return None
        row = bisect_right(self._frames, self._current_frame) - 1
        return row if row >= 0 else None

    def _state(self, frame: int) -> EventRowState:
        current = self._current_frame
        if current is None or frame < current:
            return EventRowState.PAST
        if frame == current:
            return EventRowState.CURRENT
        return EventRowState.UPCOMING


class EventLogDelegate(QStyledItemDelegate):
    """Paint event rows as frame, time and label columns."""

    def __init__(self, view: QAbstractItemView) -> None:
        super().__init__(view)
        self._view = view
        follow_appearance(self, self._load_fonts)

    def _load_fonts(self) -> None:
        self._font = TextRole.MONO.font()
        self._metrics = QFontMetrics(self._font)
        self._frame_width = self._metrics.horizontalAdvance("#99999")
        self._time_width = self._metrics.horizontalAdvance("00:00:00.000")
        self._view.doItemsLayout()

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Paint one event row."""
        event = index.data(_EVENT_ROLE)
        state = index.data(_STATE_ROLE)
        if not isinstance(event, FrameEvent) or not isinstance(state, EventRowState):
            return
        painter.save()
        painter.setClipRect(option.rect)
        rect = option.rect
        if state.background is not None:
            painter.fillRect(rect, state.background)
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style_option.text = ""
        style = style_option.widget.style() if style_option.widget is not None else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, style_option, painter, style_option.widget)
        painter.setFont(self._font)
        top = rect.top() + Space.XS
        height = self._metrics.height()
        alignment = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        frame_text = f"#{event.frame_id.sequence_id}"
        frame_width = max(self._frame_width, self._metrics.horizontalAdvance(frame_text))
        x = rect.left() + Space.S
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        muted_role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.PlaceholderText
        painter.setPen(option.palette.color(muted_role))
        painter.drawText(QRect(x, top, frame_width, height), alignment, frame_text)
        x += frame_width + Space.M
        painter.drawText(
            QRect(x, top, self._time_width, height),
            alignment,
            format_frame_time(event.frame_id.timestamp_monotime_us),
        )
        x += self._time_width + Space.M

        label_width = max(0, rect.right() - Space.S - x)
        text_role = QPalette.ColorRole.HighlightedText if selected else state.text_role
        painter.setPen(option.palette.color(text_role))
        label = self._metrics.elidedText(event.label, Qt.TextElideMode.ElideRight, label_width)
        painter.drawText(QRect(x, top, label_width, height), alignment, label)

        painter.setPen(option.palette.color(QPalette.ColorRole.Mid))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()

    @property
    def row_height(self) -> int:
        """Return the height every row has."""
        return (Space.XS * 2) + self._metrics.height()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        """Return the fixed row height."""
        return QSize(option.rect.width(), self.row_height)


class EventLogWidget(QWidget):
    """Scene event log for the side panel.

    Selecting an event emits it; double-clicking an event of a `SceneHistory` requests its frame.
    """

    eventSelected = Signal(object)  # FrameEvent
    frameRequested = Signal(int)  # video frame index

    def __init__(self, history: SceneHistory | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._history = history

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.M)

        self._search = QLineEdit(self)
        self._search.setObjectName("eventSearch")
        self._search.setPlaceholderText("Search events…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        self._kind_boxes: dict[str, QCheckBox] = {}
        self._kind_list = QWidget()
        self._kind_layout = QVBoxLayout(self._kind_list)
        self._kind_layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        menu = QMenu(self)
        kinds_action = QWidgetAction(menu)
        kinds_action.setDefaultWidget(self._kind_list)
        menu.addAction(kinds_action)
        self._filter_button = MenuButton("Filter", menu, self)
        self._filter_button.setObjectName("eventFilterButton")
        self._filter_button.setToolTip("Show only events of the checked kinds")
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addWidget(self._search, 1)
        toolbar.addWidget(self._filter_button)
        layout.addLayout(toolbar)

        self._count_label = QLabel("", self)
        self._count_label.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self._count_label)

        self._model = EventLogModel(self)
        self._list_view = QListView(self)
        self._list_view.setModel(self._model)
        self._delegate = EventLogDelegate(self._list_view)
        self._list_view.setItemDelegate(self._delegate)
        self._list_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._list_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list_view.setFrameShape(QFrame.Shape.NoFrame)
        self._list_view.setUniformItemSizes(True)
        self._list_view.clicked.connect(self._on_index_clicked)
        self._list_view.doubleClicked.connect(self._on_index_double_clicked)
        layout.addWidget(self._list_view)

        self._position_row: int | None = None
        self._current_frame: int | None = None
        self.clear()

    def set_history(self, history: SceneHistory) -> None:
        """Show the events of a replacement history."""
        self._history = history
        self.clear()
        if self.isVisible():
            self._follow_current_frame()

    def clear(self) -> None:
        """Show only the history's events, dropping live ones."""
        self._model.set_events(self._history.events if self._history is not None else ())
        self._position_row = None
        self._sync_kinds()
        self._update_count()

    def update_scene(
        self,
        scene: Scene | None,
        frame_id: FrameIdentifier | None,
        metadata: dict[str, Any] | None,
        refilter: SceneRefilter | None = None,
    ) -> None:
        """Follow the displayed frame while shown; without a history, log events of newly shown overlays.

        Live events are recorded even while hidden, since they cannot be recovered later; following the frame waits
        until the log is shown.
        """
        if frame_id is None:
            return
        if self._history is None and scene is not None and not (metadata or {}).get("overlay_reused", False):
            self._append_events([FrameEvent.of(frame_id, event) for event in scene.events])
        self._current_frame = frame_id.sequence_id
        if self.isVisible():
            self._follow_current_frame()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Catch up with the displayed frame and bring it into view when the log opens."""
        super().showEvent(event)
        self._follow_current_frame()
        if self._history is None:
            self._list_view.scrollToBottom()
        else:
            self._scroll_to_position()

    def _append_events(self, events: Sequence[FrameEvent]) -> None:
        if not events:
            return
        scroll_bar = self._list_view.verticalScrollBar()
        following = self.isVisible() and scroll_bar.value() == scroll_bar.maximum()
        self._model.append_events(events, limit=LIVE_EVENT_LIMIT)
        if any(event.kind not in self._kind_boxes for event in events):
            self._sync_kinds()
        self._update_count()
        if following:
            self._list_view.scrollToBottom()

    def _follow_current_frame(self) -> None:
        if self._current_frame is None:
            return
        self._model.set_current_frame(self._current_frame)
        position_row = self._model.position_row()
        if position_row != self._position_row:
            self._position_row = position_row
            self._scroll_to_position()

    def _scroll_to_position(self) -> None:
        """Bring the latest past event into view, a third from the top, only once it has left the view.

        Rows have one fixed height, so the scroll position follows from the row number; ``scrollTo`` would make Qt
        walk the model on every call.
        """
        if self._position_row is None or self._history is None:
            return
        scroll_bar = self._list_view.verticalScrollBar()
        row_height = self._delegate.row_height
        row_top = self._position_row * row_height
        view_height = self._list_view.viewport().height()
        if scroll_bar.value() <= row_top <= scroll_bar.value() + view_height - row_height:
            return
        scroll_bar.setValue(row_top - view_height // 3)

    def _sync_kinds(self) -> None:
        """Offer one checkbox per kind of event held, keeping the choices already made."""
        kinds = self._model.kinds()
        if list(self._kind_boxes) == kinds:
            return
        hidden = {kind for kind, box in self._kind_boxes.items() if not box.isChecked()}
        for box in self._kind_boxes.values():
            self._kind_layout.removeWidget(box)
            box.deleteLater()
        self._kind_boxes = {}
        for kind in kinds:
            box = QCheckBox(kind, self._kind_list)
            box.setChecked(kind not in hidden)
            box.toggled.connect(self._apply_filter)
            self._kind_layout.addWidget(box)
            self._kind_boxes[kind] = box
        self._filter_button.setEnabled(bool(kinds))
        self._apply_filter()

    def _apply_filter(self) -> None:
        hidden = frozenset(kind for kind, box in self._kind_boxes.items() if not box.isChecked())
        self._model.set_filter(EventFilter(self._search.text().strip(), hidden))
        shown = len(self._kind_boxes) - len(hidden)
        self._filter_button.setText("Filter" if not hidden else f"Filter {shown}/{len(self._kind_boxes)}")
        self._position_row = None
        self._update_count()
        if self.isVisible():
            self._follow_current_frame()
            if self._history is None:
                self._list_view.scrollToBottom()

    def _update_count(self) -> None:
        count, total = self._model.rowCount(), self._model.total_count
        if not total:
            self._count_label.setText("No events")
        elif count == total:
            self._count_label.setText(f"{total} event{'' if total == 1 else 's'}")
        else:
            self._count_label.setText(f"{count} of {total} events")

    def _on_index_clicked(self, index: QModelIndex) -> None:
        event = index.data(_EVENT_ROLE)
        if isinstance(event, FrameEvent):
            self.eventSelected.emit(event)

    def _on_index_double_clicked(self, index: QModelIndex) -> None:
        event = index.data(_EVENT_ROLE)
        if self._history is not None and isinstance(event, FrameEvent):
            self.frameRequested.emit(event.frame_id.sequence_id)
