"""The Objects page: a searchable list of the objects in the displayed frame, or of every object in the file."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.data_sources.scene_history import ObjectHistory, SceneHistory
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.scene.query import Query, compile_query
from ax_devil.modules.video_viewer.media_tools.object_list_rows import (
    FILE_PATHS,
    LIGHT_PATHS,
    FileRow,
    FrameRow,
    frame_columns,
    query_paths,
    row_key,
)
from ax_devil.modules.video_viewer.media_tools.object_rows import id_order
from ax_devil.modules.video_viewer.media_tools.object_scope import ObjectScope, ScopeSwitch
from ax_devil.modules.video_viewer.media_tools.page_stack import PageKey
from ax_devil.modules.video_viewer.media_tools.presence import Presence
from ax_devil.modules.video_viewer.media_tools.query_bar import QueryBar
from ax_devil.modules.video_viewer.media_tools.row_columns import CellKind, Column, Labeled
from ax_devil.modules.video_viewer.media_tools.row_model import RowTableModel
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView

_MUTED = "color: palette(placeholder-text);"
_FILE_NOTE = "File scope filters id, class and event only"


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
        self._frame_rows: dict[str, FrameRow] = {}
        self._file_key: tuple[SceneHistory, SessionFilter, Hashable] | None = None
        self._file_histories: list[ObjectHistory] = []
        self._file_rows: dict[str, FileRow] = {}
        self._file_shown: list[FileRow] = []
        self._file_dirty = True
        self._stale = True
        self._count = 0

        self._query_bar = QueryBar(self)
        self._switch = ScopeSwitch(self)
        self._switch.set_scope(self._scope)
        self._count_label = QLabel(self)
        self._count_label.setStyleSheet(_MUTED)
        TextRole.SMALL.apply(self._count_label)

        file_columns = self._file_columns()
        self._tables = {
            ObjectScope.FRAME: self._table(frame_columns(), "objects-frame", 2, Qt.SortOrder.DescendingOrder),
            ObjectScope.FILE: self._table(file_columns, "objects-file", 0, Qt.SortOrder.AscendingOrder),
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

    def update_scene(self, scene: Scene | None, frame_index: int | None, *, defer: bool = False) -> None:
        """Show the objects of *scene*, displayed at video frame *frame_index* (None when there is none)."""
        if scene is self._scene and frame_index == self._frame:
            return
        self._scene, self._frame = scene, frame_index
        self._stale = True
        if not defer:
            self.refresh()

    def refresh(self) -> None:
        """Keep the tab count current; project the latest snapshot only while this page is visible."""
        if self.isVisible() and self._stale:
            self._apply()
        elif self._scope is ObjectScope.FRAME:
            self._update_count()

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
        self._file_key, self._file_rows, self._file_dirty = None, {}, True
        self._update_count()
        self._refresh()
        self._update_switch()

    def refilter(self) -> None:
        """Re-apply the session filter to the file's objects after it changed; Frame scope has nothing to redo."""
        if self._scope is ObjectScope.FILE:
            before = self._file_key
            self._update_count()
            if self._file_key != before:
                self._refresh()

    def set_scope(self, scope: ObjectScope) -> None:
        """List the displayed frame's objects or the whole file's; File needs a history that can be filtered."""
        if scope is ObjectScope.FILE and not self._file_available():
            scope = ObjectScope.FRAME
        self._scope = scope
        self._switch.set_scope(scope)
        self._update_count()
        self._refresh()

    def neighbor(self, key: PageKey, delta: int) -> PageKey | None:
        """Return the page *delta* rows from *key* in the listed order, or None past either end or off the list.

        The order is the latest one even while the page is hidden behind an open object page; the hidden list is
        only rebuilt here, when a page asks, never as frames arrive.
        """
        if self._stale:
            self._apply()
        model = self._tables[self._scope].model
        other = model.neighbor(key, delta)
        row = None if other is None else model.row_for(other)
        return None if row is None else row_key(row)

    def highlight(self, key: PageKey | None) -> None:
        """Mark the row of *key* as the one the open page came from; a key that is not an object clears the mark."""
        for table in self._tables.values():
            table.model.set_highlight(key)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Catch up with the latest scene when the page appears."""
        super().showEvent(event)
        self.refresh()

    # Internal

    def _file_columns(self) -> tuple[Column, ...]:
        def presence(row: FileRow) -> Presence:
            frame_count = self._history.frame_count if self._history is not None else 1
            return Presence(row.history.spans, frame_count, (), self._frame, row.category)

        return (
            Column(
                "ID",
                CellKind.ID_DOT,
                lambda row: Labeled(row.entity_id, row.category),
                lambda row: id_order(row.entity_id),
            ),
            Column(
                "Class",
                CellKind.TEXT,
                lambda row: ", ".join(row.history.types),
                lambda row: ", ".join(row.history.types).casefold(),
            ),
            Column("Presence", CellKind.PRESENCE, presence, stretch=True),
            Column("Seen", CellKind.MONO, lambda row: row.seen, lambda row: row.history.spans[0][0], priority=1),
        )

    def _table(self, columns: Sequence[Column], key: str, sort_column: int, order: Qt.SortOrder) -> _ScopeTable:
        model = RowTableModel(columns, self)
        view = RowTableView(model, self, table_key=key)
        view.sort_by(sort_column, order)
        view.sortRequested.connect(self.refresh)
        view.rowActivated.connect(lambda row: self.objectActivated.emit(row.entity_id))
        return _ScopeTable(model, view)

    def _is_absent(self, row: FileRow) -> bool:
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

    def _filtered_histories(self) -> list[ObjectHistory]:
        """Return the file's objects that pass the session filter, redone only when the history or filter changed."""
        history, scene_filter = self._history, self._scene_filter
        if history is None or scene_filter is None or not scene_filter.supports_history_filtering:
            key = None
        else:
            key = (history, scene_filter, scene_filter.cache_identity)
        if key != self._file_key:
            self._file_key = key
            self._file_dirty = True
            self._file_histories = [] if key is None else key[1].filter_history(key[0].objects)
        return self._file_histories

    def _file_row(self, history: SceneHistory, objects: ObjectHistory) -> FileRow:
        """Return the row of *objects*, with the event kinds it is in; built once, when File scope first lists it."""
        row = self._file_rows.get(objects.entity_id)
        if row is None:
            kinds = tuple(dict.fromkeys(event.kind for event in history.events_involving(objects.entity_id)))
            row = self._file_rows[objects.entity_id] = FileRow(objects, kinds)
        return row

    def _on_query_changed(self, query: Query) -> None:
        self._query = query
        self._file_dirty = True
        self._refresh()

    def _update_count(self) -> None:
        if self._scope is ObjectScope.FRAME:
            count = len(self._scene.entities) if self._scene is not None else 0
        else:
            count = len(self._filtered_histories())
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
        self._update_count()
        {ObjectScope.FRAME: self._apply_frame, ObjectScope.FILE: self._apply_file}[self._scope]()

    def _apply_frame(self) -> None:
        previous = self._frame_rows
        rows: dict[str, FrameRow] = {}
        for entity in self._scene.entities.values() if self._scene is not None else ():
            known = previous.get(str(entity.id))
            rows[str(entity.id)] = known if known is not None and known.entity is entity else FrameRow(entity)
        self._frame_rows = rows
        query = self._query
        note = ""
        shown: Sequence[FrameRow] = list(rows.values())
        if not query.is_empty:
            full = not query_paths(query) <= LIGHT_PATHS
            records = [row.record(full=full) for row in shown]
            shown = [row for row, record in zip(shown, records) if query.matches(record)]
            missing = query.missing_paths(records)
            if missing:
                note = f"No object has {', '.join(missing)} in this frame"
        self._tables[ObjectScope.FRAME].model.set_rows(shown, row_key)
        self._show(shown, len(rows), note, "No objects in this frame")

    def _apply_file(self) -> None:
        histories = self._filtered_histories()
        history = self._history
        if self._file_dirty:
            self._file_dirty = False
            query = self._query
            rows = [] if history is None else [self._file_row(history, objects) for objects in histories]
            self._file_shown = [row for row in rows if query.matches(row.record)]
            self._tables[ObjectScope.FILE].model.set_rows(self._file_shown, row_key)
        else:
            self._tables[ObjectScope.FILE].view.viewport().update()
        note = "" if query_paths(self._query) <= FILE_PATHS else _FILE_NOTE
        self._show(self._file_shown, len(histories), note, "No objects in the file")

    def _show(self, shown: Sequence[Any], total: int, note: str, none_text: str) -> None:
        """Put *shown* of *total* objects in the table of the current scope, along with the note under the query."""
        table = self._tables[self._scope]
        self._query_bar.set_note(note)
        self._count_label.setText(f"{len(shown)} of {total}")
        if shown:
            self._stack.setCurrentWidget(table.view)
        else:
            self._empty.setText(none_text if total == 0 else "No matching objects")
            self._stack.setCurrentWidget(self._empty)
