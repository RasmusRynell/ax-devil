"""The Objects page: a searchable list of the objects in the displayed frame, or of every object in the file."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole
from ax_devil.modules.data_sources.scene_history import ObjectHistory, SceneHistory
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.inspection import entity_record, entity_summary
from ax_devil.modules.scene.model import Entity, Scene
from ax_devil.modules.scene.query import Query, QueryRecord, compile_query
from ax_devil.modules.video_viewer.media_tools.frame_labels import format_short_frame_time
from ax_devil.modules.video_viewer.media_tools.page_stack import ObjectPageKey, PageKey
from ax_devil.modules.video_viewer.media_tools.presence import Presence
from ax_devil.modules.video_viewer.media_tools.query_bar import QueryBar
from ax_devil.modules.video_viewer.media_tools.row_table import CellKind, Column, Labeled, RowTableModel, RowTableView

_FILE_NOTE = "File scope filters id, class and events. Attribute filters apply in Frame scope."
_MUTED = "color: palette(placeholder-text);"
_DIGITS = re.compile(r"(\d+)")
# Paths every record has, whatever the scope, so a query that names only these never needs the entity's full fields.
_ID = "id"
_CLASS = "class"
_EVENT = "event"
_LIGHT_PATHS = frozenset({_ID, _CLASS})
_SWITCH_STYLE = f"""
    #objectScope {{ border: 1px solid palette(mid); border-radius: {Radius.CONTROL}px; }}
    #objectScope QToolButton {{
        border: 0px;
        border-radius: {Radius.CONTROL - 1}px;
        padding: 0px {Space.M}px;
        background: transparent;
    }}
    #objectScope QToolButton:hover {{ background: palette(midlight); }}
    #objectScope QToolButton:checked {{ color: palette(highlight); background: palette(midlight); }}
"""


class ObjectScope(Enum):
    """Which objects the page lists: those in the displayed frame, or every object in the file."""

    FRAME = "Frame"
    FILE = "File"

    @property
    def title(self) -> str:
        """Return the scope switch label."""
        return self.value

    @property
    def tooltip(self) -> str:
        """Return the scope switch help."""
        return {
            ObjectScope.FRAME: "Objects in the displayed frame, searchable by any attribute",
            ObjectScope.FILE: "Every object in the file; objects not in the displayed frame are dimmed",
        }[self]


def _id_order(text: str) -> tuple[tuple[int, str], ...]:
    """Return *text* as a sort key that orders embedded numbers by value, so ``"id9"`` comes before ``"id10"``."""
    return tuple((int(part), "") if part.isdigit() else (-1, part) for part in _DIGITS.split(text))


def _key(row: Any) -> ObjectPageKey:
    """Identify a row by the page it opens."""
    return ObjectPageKey(row.entity_id)


def _query_paths(query: Query) -> frozenset[str]:
    """Return the lower-cased field paths the query names."""
    return frozenset(term.path.lower() for group in query.groups for term in group if term.path is not None)


class _FrameRow:
    """An object of the displayed frame; its searchable record is built when a query first asks for it."""

    def __init__(self, entity: Entity) -> None:
        self.entity = entity
        self.summary = entity_summary(entity)
        self.entity_id = self.summary.entity_id

    def record(self, *, full: bool) -> QueryRecord:
        """Return what a query searches: id, class and state, plus every field of the entity when *full*."""
        return self._full_record if full else self._light_record

    @cached_property
    def _light_record(self) -> QueryRecord:
        summary = self.summary
        fields = {_ID: (summary.entity_id,), _CLASS: (summary.class_name,)}
        state = () if summary.state is None else (summary.state,)
        return QueryRecord(fields=fields, text=(summary.entity_id, summary.class_name, *state))

    @cached_property
    def _full_record(self) -> QueryRecord:
        record = entity_record(self.entity)
        return QueryRecord(fields={**record.fields, **self._light_record.fields}, text=record.text)


class _FileRow:
    """An object of the whole file, searchable by id, class and the kinds of events it is in."""

    def __init__(self, history: ObjectHistory, event_kinds: tuple[str, ...]) -> None:
        self.history = history
        self.entity_id = history.entity_id
        self.record = QueryRecord(
            fields={_ID: (history.entity_id,), _CLASS: history.types, _EVENT: event_kinds},
            text=(history.entity_id, *history.types),
        )

    @property
    def category(self) -> str:
        """Return the class that colors the object."""
        return self.history.types[0] if self.history.types else ""

    @cached_property
    def seen(self) -> str:
        """Return when the object is first and last shown."""
        first, last = self.history.first_seen, self.history.last_seen
        start = format_short_frame_time(first.timestamp_monotime_us)
        return f"{start}–{format_short_frame_time(last.timestamp_monotime_us)}"


def _confidence_text(conf: float | None) -> str:
    if conf is None:
        return ""
    return f"{conf:.0%}" if 0.0 <= conf <= 1.0 else f"{conf:g}"


def _frame_columns() -> tuple[Column, ...]:
    return (
        Column(
            "ID",
            CellKind.ID_DOT,
            lambda row: Labeled(row.entity_id, row.summary.class_name),
            lambda row: _id_order(row.entity_id),
            width_chars=8,
        ),
        Column(
            "Class",
            CellKind.TEXT,
            lambda row: row.summary.class_name,
            lambda row: row.summary.class_name.casefold(),
            stretch=True,
        ),
        Column(
            "Conf",
            CellKind.NUMBER,
            lambda row: _confidence_text(row.summary.conf),
            lambda row: (row.summary.conf is not None, row.summary.conf or 0.0),
            width_chars=4,
        ),
        Column(
            "Motion",
            CellKind.TEXT,
            lambda row: row.summary.state or "",
            lambda row: row.summary.state or "",
            width_chars=10,
        ),
        Column("", CellKind.CHEVRON, lambda row: None),
    )


class _ScopeSwitch(QFrame):
    """The Frame | File switch: one framed control whose chosen half is marked like the current tab."""

    scopeSelected = Signal(object)  # noqa: N815  # ObjectScope

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("objectScope")
        self.setStyleSheet(_SWITCH_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        group = QButtonGroup(self)
        self._buttons: dict[ObjectScope, QToolButton] = {}
        for scope in ObjectScope:
            button = QToolButton(self)
            button.setText(scope.title)
            button.setToolTip(scope.tooltip)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            TextRole.SMALL.apply(button)
            button.clicked.connect(lambda _checked=False, chosen=scope: self.scopeSelected.emit(chosen))
            group.addButton(button)
            layout.addWidget(button)
            self._buttons[scope] = button
        follow_appearance(self, lambda: self.setFixedHeight(Height.CONTROL.px))

    def set_scope(self, scope: ObjectScope) -> None:
        """Mark *scope* as the chosen one."""
        self._buttons[scope].setChecked(True)


@dataclass(frozen=True, slots=True)
class _ScopeTable:
    """The table of one scope; each keeps its own sort and scroll position."""

    model: RowTableModel
    view: RowTableView


class ObjectsPage(QWidget):
    """Searchable list of objects, in the displayed frame or (offline) in the whole file.

    Frame scope lists the displayed Scene's entities and searches any attribute of them; File scope lists every object
    of the ``SceneHistory`` that passes the session filter, searchable by id, class and the kinds of events it is in.
    A click or Enter on a row emits ``objectActivated`` with the object's id. The page is a ``RowSource``: it orders
    the objects the object page steps through and marks the one that page came from.

    The query, scope and sort stay as they are across scene updates. While the page is hidden it keeps only the
    latest scene and frame, and catches up when shown.
    """

    objectActivated = Signal(str)  # noqa: N815  # entity id
    countChanged = Signal(int)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty page in Frame scope."""
        super().__init__(parent)
        self._scene: Scene | None = None
        self._frame: int | None = None
        self._query = compile_query("")
        self._scope = ObjectScope.FRAME
        self._history: SceneHistory | None = None
        self._scene_filter: SessionFilter | None = None
        self._frame_rows: dict[str, _FrameRow] = {}
        self._file_rows: list[_FileRow] = []
        self._file_shown: list[_FileRow] = []
        self._file_dirty = True
        self._stale = True
        self._count = 0
        self._highlight: ObjectPageKey | None = None

        self._query_bar = QueryBar(self)
        self._switch = _ScopeSwitch(self)
        self._switch.set_scope(self._scope)
        self._count_label = QLabel(self)
        self._count_label.setStyleSheet(_MUTED)
        TextRole.SMALL.apply(self._count_label)

        frame_columns = _frame_columns()
        file_columns = self._file_columns()
        self._tables = {
            ObjectScope.FRAME: self._table(frame_columns, 2, Qt.SortOrder.DescendingOrder),
            ObjectScope.FILE: self._table(file_columns, 0, Qt.SortOrder.AscendingOrder),
        }
        self._tables[ObjectScope.FILE].model.set_dimmed(self._is_absent)
        self._empty = QLabel(self)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._empty.setStyleSheet(_MUTED)
        self._empty.setContentsMargins(0, Space.L, 0, 0)
        TextRole.SMALL.apply(self._empty)

        self._stack = QStackedLayout()
        for table in self._tables.values():
            self._stack.addWidget(table.view)
        self._stack.addWidget(self._empty)

        controls = QWidget(self)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(Space.M, Space.M, Space.M, Space.S)
        controls_layout.setSpacing(Space.S)
        controls_layout.addWidget(self._query_bar)
        scope_row = QHBoxLayout()
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.addWidget(self._switch)
        scope_row.addStretch(1)
        scope_row.addWidget(self._count_label)
        controls_layout.addLayout(scope_row)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(controls)
        layout.addLayout(self._stack, 1)

        self._query_bar.queryChanged.connect(self._on_query_changed)
        self._switch.scopeSelected.connect(self.set_scope)
        self._update_switch()
        self._refresh()

    @property
    def count(self) -> int:
        """Return how many objects the current scope lists before the query narrows them."""
        return self._count

    @property
    def scope(self) -> ObjectScope:
        """Return the scope in use."""
        return self._scope

    def update_scene(self, scene: Scene | None, frame_index: int | None) -> None:
        """Show the objects of *scene*, displayed at video frame *frame_index* (None when there is none)."""
        if scene is self._scene and frame_index == self._frame:
            return
        self._scene, self._frame = scene, frame_index
        self._update_count()
        self._refresh()

    def clear(self) -> None:
        """Forget the displayed scene; the file's history and the query stay."""
        self._scene, self._frame = None, None
        self._frame_rows = {}
        self._update_count()
        self._refresh()

    def set_history(self, history: SceneHistory | None, scene_filter: SessionFilter | None) -> None:
        """Offer the File scope for *history*, filtered by *scene_filter* like the overlay; None removes it.

        The scope is only offered when *scene_filter* can filter history, which needs the decoder's classification
        policies.
        """
        self._history, self._scene_filter = history, scene_filter
        self._build_file_rows()
        self._update_switch()

    def refilter(self) -> None:
        """Re-apply the session filter to the file's objects after it changed."""
        self._build_file_rows()

    def set_scope(self, scope: ObjectScope) -> None:
        """List the displayed frame's objects or the whole file's; File needs a history that can be filtered."""
        if scope is ObjectScope.FILE and not self._file_available():
            scope = ObjectScope.FRAME
        self._scope = scope
        self._switch.set_scope(scope)
        self._update_count()
        self._refresh()

    def set_current_object(self, entity_id: str | None) -> None:
        """Mark the row of the object whose page is open; None clears the mark."""
        self.highlight(None if entity_id is None else ObjectPageKey(entity_id))

    def neighbor(self, key: PageKey, delta: int) -> PageKey | None:
        """Return the page *delta* rows from *key* in the listed order, or None past either end or off the list."""
        model = self._tables[self._scope].model
        other = model.neighbor(key, delta)
        row = None if other is None else model.row_for(other)
        return None if row is None else _key(row)

    def highlight(self, key: PageKey | None) -> None:
        """Mark the row of *key* as the one the open page came from; a key that is not an object clears the mark."""
        for table in self._tables.values():
            table.model.set_highlight(key)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Catch up with the latest scene when the page appears."""
        super().showEvent(event)
        if self._stale:
            self._apply()

    # Internal

    def _file_columns(self) -> tuple[Column, ...]:
        def presence(row: _FileRow) -> Presence:
            frame_count = self._history.frame_count if self._history is not None else 1
            return Presence(row.history.spans, frame_count, (), self._frame, row.category)

        return (
            Column(
                "ID",
                CellKind.ID_DOT,
                lambda row: Labeled(row.entity_id, row.category),
                lambda row: _id_order(row.entity_id),
                width_chars=8,
            ),
            Column(
                "Class",
                CellKind.TEXT,
                lambda row: ", ".join(row.history.types),
                lambda row: ", ".join(row.history.types).casefold(),
                stretch=True,
            ),
            Column("Presence", CellKind.PRESENCE, presence, width_chars=7),
            Column("Seen", CellKind.MONO, lambda row: row.seen, lambda row: row.history.spans[0][0], width_chars=11),
            Column("", CellKind.CHEVRON, lambda row: None),
        )

    def _table(self, columns: Sequence[Column], sort_column: int, order: Qt.SortOrder) -> _ScopeTable:
        model = RowTableModel(columns, self)
        view = RowTableView(model, self)
        view.sort_by(sort_column, order)
        view.rowActivated.connect(lambda row: self.objectActivated.emit(row.entity_id))
        return _ScopeTable(model, view)

    def _is_absent(self, row: _FileRow) -> bool:
        return self._frame is not None and not row.history.is_visible_at(self._frame)

    def _file_available(self) -> bool:
        return (
            self._history is not None
            and self._scene_filter is not None
            and self._scene_filter.supports_history_filtering
        )

    def _update_switch(self) -> None:
        available = self._file_available()
        self._switch.setVisible(available)
        if not available and self._scope is ObjectScope.FILE:
            self.set_scope(ObjectScope.FRAME)

    def _build_file_rows(self) -> None:
        """Make a row of each object that passes the session filter, with the event kinds it is in, once."""
        history, scene_filter = self._history, self._scene_filter
        if history is None or scene_filter is None or not scene_filter.supports_history_filtering:
            self._file_rows = []
        else:
            self._file_rows = [
                _FileRow(
                    objects,
                    tuple(dict.fromkeys(event.kind for event in history.events_involving(objects.entity_id))),
                )
                for objects in scene_filter.filter_history(history.objects)
            ]
        self._file_dirty = True
        self._update_count()
        self._refresh()

    def _on_query_changed(self, query: Query) -> None:
        self._query = query
        self._file_dirty = True
        self._refresh()

    def _update_count(self) -> None:
        if self._scope is ObjectScope.FRAME:
            count = len(self._scene.entities) if self._scene is not None else 0
        else:
            count = len(self._file_rows)
        if count != self._count:
            self._count = count
            self.countChanged.emit(count)

    def _refresh(self) -> None:
        if self.isVisible():
            self._apply()
        else:
            self._stale = True

    def _apply(self) -> None:
        self._stale = False
        {ObjectScope.FRAME: self._apply_frame, ObjectScope.FILE: self._apply_file}[self._scope]()

    def _apply_frame(self) -> None:
        previous = self._frame_rows
        rows: dict[str, _FrameRow] = {}
        for entity in self._scene.entities.values() if self._scene is not None else ():
            known = previous.get(str(entity.id))
            rows[str(entity.id)] = known if known is not None and known.entity is entity else _FrameRow(entity)
        self._frame_rows = rows
        query = self._query
        note = ""
        shown: Sequence[_FrameRow] = list(rows.values())
        if not query.is_empty:
            full = not _query_paths(query) <= _LIGHT_PATHS
            records = [row.record(full=full) for row in shown]
            shown = [row for row, record in zip(shown, records) if query.matches(record)]
            missing = query.missing_paths(records)
            if missing:
                note = f"No object has {', '.join(missing)} in this frame"
        self._show(shown, len(rows), note, "No objects in this frame")

    def _apply_file(self) -> None:
        if self._file_dirty:
            self._file_dirty = False
            query = self._query
            self._file_shown = [row for row in self._file_rows if query.matches(row.record)]
        self._show(self._file_shown, len(self._file_rows), _FILE_NOTE, "No objects in the file")

    def _show(self, shown: Sequence[Any], total: int, note: str, none_text: str) -> None:
        """Put *shown* of *total* objects in the table of the current scope, along with the note under the query."""
        table = self._tables[self._scope]
        table.model.set_rows(shown, _key)
        self._query_bar.set_note(note)
        self._count_label.setText(f"{len(shown)} of {total}")
        if shown:
            self._stack.setCurrentWidget(table.view)
        else:
            self._empty.setText(none_text if total == 0 else "No matching objects")
            self._stack.setCurrentWidget(self._empty)
