"""The Events page: a searchable list of the Scene events of a video, or (live) of the newest events shown."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from functools import cached_property
from typing import Any

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPaintEvent, QPalette, QShowEvent
from PySide6.QtWidgets import QLabel, QStackedLayout, QStyleOptionViewItem, QVBoxLayout, QWidget

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.scene.query import Query, QueryRecord, compile_query
from ax_devil.modules.video_viewer.media_tools.frame_labels import current_frame_highlight, format_frame_time
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey, PageKey
from ax_devil.modules.video_viewer.media_tools.query_bar import QueryBar
from ax_devil.modules.video_viewer.media_tools.row_table import CellKind, Column, Labeled, RowTableModel, RowTableView

LIVE_EVENT_LIMIT = 1000
"""Live pages keep only this many of the newest events."""

_MUTED = "color: palette(placeholder-text);"
_PLACEHOLDER = "Search events"
_SYNTAX = (
    ("kind:merge", "kind of event"),
    ("object:17", "involves object 17"),
    ("moved", "text in the label"),
    ("-kind:delete", "exclude matches"),
    ("a OR b", "either term"),
    ("a b", "both terms (default)"),
)
_NOW_LINE_PX = 2
_KIND = "kind"
_OBJECT = "object"


class _EventRow:
    """An event of the list; what a query searches is built when a query first asks for it."""

    def __init__(self, event: FrameEvent) -> None:
        self.event = event
        self.frame = event.frame_id.sequence_id

    @cached_property
    def record(self) -> QueryRecord:
        """Return what a query searches: the kind and the involved objects as fields, the label as free text."""
        event = self.event
        return QueryRecord(fields={_KIND: (event.kind,), _OBJECT: event.entity_ids}, text=(event.label,))


def _key(row: Any) -> EventPageKey:
    """Identify a row by the page it opens."""
    return EventPageKey(row.event)


class _EventsTable(RowTableView):
    """The events table, with the rows of the displayed frame tinted and a line where the past ends.

    The page says where that is as a pair of row numbers, its rows being in frame order: the rows from the first up to
    the second are of the displayed frame, and the line lies just before the second row.
    """

    def __init__(self, model: RowTableModel, parent: QWidget | None = None) -> None:
        super().__init__(model, parent)
        self._position: tuple[int, int] | None = None

    @property
    def position(self) -> tuple[int, int] | None:
        """Return the first row of the displayed frame and the row the line lies before, or None without a frame."""
        return self._position

    def set_position(self, position: tuple[int, int] | None) -> None:
        """Mark rows ``position[0]`` up to ``position[1]`` as the displayed frame's, and the line after the last."""
        if position != self._position:
            self._position = position
            self.viewport().update()

    def drawRow(  # noqa: N802
        self, painter: QPainter, options: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Tint the row if it belongs to the displayed frame, then draw it."""
        if self._position is not None and self._position[0] <= index.row() < self._position[1]:
            rect = QRect(0, options.rect.top(), self.viewport().width(), options.rect.height())
            painter.fillRect(rect, current_frame_highlight(QGuiApplication.palette()))
        super().drawRow(painter, options, index)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the rows, then the line between the events that have happened and those still to come."""
        super().paintEvent(event)
        if self._position is None:
            return
        rows = self.rows_model.rowCount()
        now_row = self._position[1]
        if rows == 0:
            return
        if now_row < rows:
            y = self.visualRect(self.rows_model.index(now_row, 0)).top()
        else:
            y = self.visualRect(self.rows_model.index(rows - 1, 0)).bottom() + 1
        painter = QPainter(self.viewport())
        painter.fillRect(
            QRect(0, y - _NOW_LINE_PX // 2, self.viewport().width(), _NOW_LINE_PX),
            QGuiApplication.palette().color(QPalette.ColorRole.Highlight),
        )
        painter.end()


class EventsPage(QWidget):
    """Searchable list of the Scene events (deletes, renames, merges, splits) of the video or live stream.

    With a ``SceneHistory`` the page lists every event of the overlay source in frame order; without one (live) it
    keeps the newest ``LIVE_EVENT_LIMIT`` events as their overlays are shown, and records them even while hidden since
    they cannot be recovered later. Rows of the displayed frame are tinted and a line follows playback between the
    events that have happened and those still to come; offline, the list scrolls to keep it in view unless the user has
    scrolled away from it. Queries search the fields ``kind`` and ``object`` (any involved id) and the label as free
    text.

    A click or Enter on a row emits ``eventActivated`` with its ``FrameEvent``. The page is a ``RowSource``: it orders
    the events the event page steps through and marks the one that page came from. While hidden it keeps only the
    latest frame and applies it when shown, and keeps the scroll position the user left it at.
    """

    eventActivated = Signal(object)  # noqa: N815  # FrameEvent
    countChanged = Signal(int)  # noqa: N815  # events held, before the query narrows them
    rowsChanged = Signal()  # noqa: N815  # events were added, dropped or replaced; at the live limit the count stays

    def __init__(self, history: SceneHistory | None = None, parent: QWidget | None = None) -> None:
        """Create the page for *history* (None for a live stream)."""
        super().__init__(parent)
        self._history = history
        self._rows: list[_EventRow] = []
        self._shown: list[_EventRow] = []
        self._shown_frames: list[int] = []
        self._query = compile_query("")
        self._frame: int | None = None
        self._stale = True
        self._following = True
        self._scrolling = False
        self._count = 0

        self._query_bar = QueryBar(self, placeholder=_PLACEHOLDER, syntax=_SYNTAX)
        self._count_label = QLabel(self)
        self._count_label.setStyleSheet(_MUTED)
        TextRole.SMALL.apply(self._count_label)
        self._count_label.hide()

        self._model = RowTableModel(self._columns(), self)
        self._model.set_dimmed(self._is_upcoming)
        self._view = _EventsTable(self._model, self)
        self._empty = QLabel(self)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._empty.setStyleSheet(_MUTED)
        self._empty.setContentsMargins(0, Space.L, 0, 0)
        TextRole.SMALL.apply(self._empty)

        self._stack = QStackedLayout()
        self._stack.addWidget(self._view)
        self._stack.addWidget(self._empty)

        controls = QWidget(self)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(Space.M, Space.M, Space.M, Space.S)
        controls_layout.setSpacing(Space.S)
        controls_layout.addWidget(self._query_bar)
        controls_layout.addWidget(self._count_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(controls)
        layout.addLayout(self._stack, 1)

        self._query_bar.queryChanged.connect(self._on_query_changed)
        self._view.rowActivated.connect(lambda row: self.eventActivated.emit(row.event))
        self._view.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.set_history(history)

    @property
    def count(self) -> int:
        """Return how many events the page holds, before the query narrows them."""
        return self._count

    def set_history(self, history: SceneHistory | None) -> None:
        """List the events of *history* in frame order, or start an empty live list with None; drops live events."""
        self._history = history
        events = sorted(history.events, key=lambda event: event.frame_id.sequence_id) if history is not None else ()
        self._rows = [_EventRow(event) for event in events]
        self._following = True
        self._changed()

    def clear(self) -> None:
        """Drop the live events and forget the displayed frame; a history's events stay."""
        if self._history is None:
            self._rows = []
        self._frame = None
        self._changed()

    def update_scene(
        self, scene: Scene | None, frame_id: FrameIdentifier | None, metadata: Mapping[str, Any] | None = None
    ) -> None:
        """Follow the displayed frame; live, also record the events of *scene* when its overlay is newly shown.

        An overlay *metadata* marks as reused is the previous frame's again, whose events are already recorded.
        """
        if frame_id is None:
            return
        if self._history is None and scene is not None and not (metadata or {}).get("overlay_reused", False):
            self._record([FrameEvent.of(frame_id, event) for event in scene.events])
        frame = frame_id.sequence_id
        if frame != self._frame:
            self._frame = frame
            self._follow_frame()

    def neighbor(self, key: PageKey, delta: int) -> PageKey | None:
        """Return the page *delta* rows from *key* in the listed order, or None past either end or off the list.

        The order is the latest one even while the page is hidden behind an open event page.
        """
        if self._stale:
            self._apply()
        other = self._model.neighbor(key, delta)
        row = None if other is None else self._model.row_for(other)
        return None if row is None else _key(row)

    def highlight(self, key: PageKey | None) -> None:
        """Mark the row of *key* as the one the open page came from; a key that is not an event clears the mark."""
        self._model.set_highlight(key)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Catch up with the recorded events and the displayed frame; bring the line into view if it was followed.

        A user who scrolled away from the line finds the list where they left it, such as after going back from an
        event page.
        """
        super().showEvent(event)
        if self._stale:
            self._apply()
        self._follow_frame(force=self._following)
        if self._history is None and self._following:
            self._view.scrollToBottom()

    # Internal

    def _columns(self) -> tuple[Column, ...]:
        def chips(row: _EventRow) -> tuple[Labeled, ...]:
            return tuple(
                Labeled(entity_id, self._class_of(entity_id) or row.event.kind) for entity_id in row.event.entity_ids
            )

        return (
            Column(
                "Time",
                CellKind.MONO,
                lambda row: format_frame_time(row.event.frame_id.timestamp_monotime_us),
                width_chars=14,
            ),
            Column("Kind", CellKind.TAG, lambda row: Labeled(row.event.kind.lower(), row.event.kind), width_chars=8),
            Column("Objects", CellKind.ID_CHIPS, chips, stretch=True),
            Column("", CellKind.CHEVRON, lambda row: None),
        )

    def _class_of(self, entity_id: str) -> str:
        """Return the class that colors an object's chip, or an empty string when the history does not know it."""
        history = self._history.object(entity_id) if self._history is not None else None
        return history.types[0] if history is not None and history.types else ""

    def _is_upcoming(self, row: _EventRow) -> bool:
        return self._frame is not None and row.frame > self._frame

    def _record(self, events: Sequence[FrameEvent]) -> None:
        """Append live *events*, dropping the oldest beyond the limit."""
        if not events:
            return
        scroll_bar = self._view.verticalScrollBar()
        following = self.isVisible() and scroll_bar.value() == scroll_bar.maximum()
        self._rows.extend(_EventRow(event) for event in events)
        del self._rows[: max(0, len(self._rows) - LIVE_EVENT_LIMIT)]
        self._changed()
        if following and self.isVisible():
            self._view.scrollToBottom()

    def _on_query_changed(self, query: Query) -> None:
        self._query = query
        self._refresh()

    def _on_scrolled(self) -> None:
        """Stop following the line when the user scrolls it out of view, and follow again when they bring it back."""
        if not self._scrolling:
            self._following = self._line_in_view()

    def _changed(self) -> None:
        """Count the events held and show them, now or when the page is shown."""
        self._refresh()
        if len(self._rows) != self._count:  # After the refresh, so a listener asking for neighbors sees the new rows.
            self._count = len(self._rows)
            self.countChanged.emit(self._count)
        self.rowsChanged.emit()

    def _refresh(self) -> None:
        if self.isVisible():
            self._apply()
        else:
            self._stale = True

    def _apply(self) -> None:
        """Put the events the query accepts in the table."""
        self._stale = False
        rows, query = self._rows, self._query
        note = ""
        shown = rows
        if not query.is_empty:
            records = [row.record for row in rows]
            shown = [row for row, record in zip(rows, records) if query.matches(record)]
            missing = query.missing_paths(records)
            if missing:
                note = f"No event has {', '.join(missing)}"
        self._shown = list(shown)
        self._shown_frames = [row.frame for row in self._shown]
        self._scrolling = True
        try:
            self._model.set_rows(self._shown, _key)
        finally:
            self._scrolling = False
        self._query_bar.set_note(note)
        self._count_label.setText(f"{len(self._shown)} of {len(rows)} events")
        self._count_label.setVisible(not query.is_empty)
        if self._shown:
            self._stack.setCurrentWidget(self._view)
        else:
            self._empty.setText("No events" if not rows else "No matching events")
            self._stack.setCurrentWidget(self._empty)
        self._follow_frame()

    def _follow_frame(self, *, force: bool = False) -> None:
        """Move the line to the displayed frame, and the view with it if it was following the line."""
        if not self.isVisible():
            return
        frame = self._frame
        if frame is None:
            self._view.set_position(None)
            return
        position = (bisect_left(self._shown_frames, frame), bisect_right(self._shown_frames, frame))
        moved = self._view.position != position
        self._view.set_position(position)
        if self._history is not None and (force or (moved and self._following)):
            self._scroll_to_line()

    def _line_in_view(self) -> bool:
        position = self._view.position
        if position is None:
            return True
        scroll_bar = self._view.verticalScrollBar()
        row_height = Height.ROW.px
        row_top = max(position[1] - 1, 0) * row_height
        return scroll_bar.value() <= row_top <= scroll_bar.value() + self._view.viewport().height() - row_height

    def _scroll_to_line(self) -> None:
        """Bring the latest event that has happened into view, a third from the top, only once it has left the view.

        Rows have one fixed height, so the scroll position follows from the row number; ``scrollTo`` would make Qt
        walk the model on every call.
        """
        position = self._view.position
        if position is None or self._line_in_view():
            return
        row_top = max(position[1] - 1, 0) * Height.ROW.px
        self._scrolling = True
        try:
            self._view.verticalScrollBar().setValue(row_top - self._view.viewport().height() // 3)
        finally:
            self._scrolling = False
