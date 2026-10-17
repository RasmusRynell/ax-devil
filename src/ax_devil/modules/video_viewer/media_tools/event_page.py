"""The page of one event: what happened, when, which objects it involves and what else happens at that moment."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPaintEvent, QPalette, QShowEvent
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chip import paint_tag
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import SEE_THROUGH, category_color
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.object_inspection.frame_labels import format_frame_time
from ax_devil.modules.scene.inspection import entity_summary
from ax_devil.modules.scene.model import EntityId, Scene
from ax_devil.modules.video_viewer.media_tools.object_rows import id_order, seen_range
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey
from ax_devil.modules.video_viewer.media_tools.presence import Presence
from ax_devil.modules.video_viewer.media_tools.row_table import CellKind, Column, Labeled, RowTableModel, RowTableView

_MUTED = "color: palette(placeholder-text); border: none; background: transparent;"
_PLAIN = "border: none; background: transparent;"
_OBJECTS = "Objects involved"
_SAME_MOMENT = "Same moment"


def event_summary(event: FrameEvent) -> str:
    """Return what the event's label says beyond its kind, such as ``12 → 17`` for a merge, or the whole label."""
    return event.label.removeprefix(event.kind).strip() or event.label


@dataclass(frozen=True, slots=True)
class _Involved:
    """An object an event involves: its id, where it appears if the file's history knows it, and its classes."""

    entity_id: str
    history: ObjectHistory | None
    classes: tuple[str, ...]

    @property
    def category(self) -> str:
        """Return the class that colors the object."""
        return self.classes[0] if self.classes else ""


class _TitleRow(QWidget):
    """The event's kind as a colored tag, followed by its summary."""

    def __init__(self, event: FrameEvent, parent: QWidget) -> None:
        super().__init__(parent)
        self._kind = event.kind
        self._summary = event_summary(event)
        self.setStyleSheet(_PLAIN)
        self.setToolTip(event.label)
        follow_appearance(self, lambda: self.setFixedHeight(Height.PANE_HEADER.px))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the kind tag, then the summary elided to the room left."""
        painter = QPainter(self)
        palette = self.palette()
        rect = self.rect().adjusted(Space.M, 0, -Space.M, 0)
        tag_width = paint_tag(painter, rect, self._kind, category_color(self._kind, palette))
        left = rect.left() + tag_width + Space.M
        if left >= rect.right():
            return
        font = TextRole.STRONG.font()
        shown = QFontMetrics(font).elidedText(self._summary, Qt.TextElideMode.ElideRight, rect.right() - left + 1)
        painter.setFont(font)
        painter.setPen(palette.color(QPalette.ColorRole.Text))
        painter.drawText(
            QRect(left, rect.top(), rect.right() - left + 1, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            shown,
        )


class EventPage(QWidget):
    """One event: its kind and summary, when it happens, the objects it involves and the events of the same moment.

    *Go to frame* and the events of the same moment need a ``SceneHistory``: a live stream has none, and its frames
    cannot be sought, so there the page shows the title, the time and the objects only. An involved object is dimmed
    while it is not in the displayed frame. Clicking one emits ``objectActivated`` with its id and clicking another
    event emits ``eventActivated`` with its ``EventPageKey``; an equal event reported twice is another event, so the
    page leaves out only its own occurrence. A page that is not shown keeps only the latest frame and catches up when
    it is.
    """

    frameRequested = Signal(int)  # video frame index
    objectActivated = Signal(str)  # entity id
    eventActivated = Signal(object)  # EventPageKey

    def __init__(self, page_key: EventPageKey, parent: QWidget | None = None) -> None:
        """Create the page of the event *page_key* names."""
        super().__init__(parent)
        self._key = page_key
        self._event = event = page_key.event
        self._history: SceneHistory | None = None
        self._scene: Scene | None = None
        self._frame: int | None = None
        self._simultaneous: tuple[EventPageKey, ...] = ()
        self._dirty = True
        self.setStyleSheet(SEE_THROUGH)

        self._go_button = SmallButton("Go to frame", parent=self)
        self._go_button.clicked.connect(lambda: self.frameRequested.emit(self._event.frame_id.sequence_id))
        details = QWidget(self)
        grid = QGridLayout(details)
        grid.setContentsMargins(Space.M, Space.S, Space.M, Space.S)
        grid.setHorizontalSpacing(Space.M)
        grid.setVerticalSpacing(Space.XS)
        frame_id = event.frame_id
        for row, (name, value) in enumerate(
            (("Time", format_frame_time(frame_id.timestamp_monotime_us)), ("Frame", f"#{frame_id.sequence_id}"))
        ):
            key = QLabel(name, details)
            key.setStyleSheet(_MUTED)
            TextRole.SMALL.apply(key)
            text = QLabel(value, details)
            text.setStyleSheet(_PLAIN)
            TextRole.MONO.apply(text)
            grid.addWidget(key, row, 0)
            grid.addWidget(text, row, 1)
        grid.setColumnStretch(2, 1)
        grid.addWidget(self._go_button, 0, 3, 2, 1, Qt.AlignmentFlag.AlignVCenter)

        self._objects_model = RowTableModel(self._object_columns(), self)
        self._objects_model.set_dimmed(self._is_absent)
        self._objects = RowTableView(self._objects_model, self)
        self._objects.rowActivated.connect(lambda row: self.objectActivated.emit(row.entity_id))
        self._moment_model = RowTableModel(
            (
                Column("Kind", CellKind.TAG, lambda other: Labeled(other.event.kind, other.event.kind), width_chars=9),
                Column("Event", CellKind.TEXT, lambda other: event_summary(other.event), stretch=True),
                Column("", CellKind.CHEVRON, lambda other: None),
            ),
            self,
        )
        self._moment = RowTableView(self._moment_model, self)
        self._moment.setHeaderHidden(True)
        self._moment.rowActivated.connect(self.eventActivated)

        self._objects_caption = self._caption(_OBJECTS, len(self._unique_ids()))
        self._moment_count = 0
        self._moment_caption = self._caption(_SAME_MOMENT, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(_TitleRow(event, self))
        layout.addWidget(details)
        layout.addWidget(self._objects_caption)
        layout.addWidget(self._objects, 1)
        layout.addWidget(self._moment_caption)
        layout.addWidget(self._moment, 1)
        layout.addStretch(0)
        follow_appearance(self, self._fit_tables)

    @property
    def frame_event(self) -> FrameEvent:
        """Return the event this page shows."""
        return self._event

    def set_history(self, history: SceneHistory | None) -> None:
        """Offer *Go to frame*, the objects' presence and the events of the same moment from *history*; ``None``
        (a live stream) shows none of them.
        """
        self._history = history
        sequence = self._event.frame_id.sequence_id
        seen: Counter[FrameEvent] = Counter()
        keys: list[EventPageKey] = []
        for other in () if history is None else history.events:
            key = EventPageKey(other, seen[other])
            seen[other] += 1
            if other.frame_id.sequence_id == sequence and key != self._key:
                keys.append(key)
        self._simultaneous = tuple(keys)
        self._request_refresh()

    def update_scene(self, scene: Scene | None, frame_index: int) -> None:
        """Follow the displayed *frame_index* and its *scene*, which tell which involved objects are in the frame."""
        self._scene, self._frame = scene, frame_index
        self._request_refresh()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Catch up with the latest frame and history."""
        super().showEvent(event)
        if self._dirty:
            self._refresh()

    # Internal

    def _caption(self, title: str, count: int) -> SectionCaption:
        return SectionCaption(title, count, lambda palette: category_color(title, palette))

    def _unique_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self._event.entity_ids))

    def _object_columns(self) -> tuple[Column, ...]:
        def presence(row: _Involved) -> Presence:
            history = self._history
            return Presence(
                row.history.spans if row.history is not None else (),
                history.frame_count if history is not None else 1,
                (self._event.frame_id.sequence_id,),
                self._frame,
                row.category,
            )

        return (
            Column(
                "ID",
                CellKind.ID_DOT,
                lambda row: Labeled(row.entity_id, row.category),
                lambda row: id_order(row.entity_id),
                width_chars=8,
            ),
            Column(
                "Class",
                CellKind.TEXT,
                lambda row: ", ".join(row.classes),
                lambda row: ", ".join(row.classes).casefold(),
                stretch=True,
            ),
            Column("Presence", CellKind.PRESENCE, presence, width_chars=7),
            Column(
                "Seen",
                CellKind.MONO,
                lambda row: seen_range(row.history) if row.history is not None else "",
                lambda row: row.history.spans[0][0] if row.history is not None else -1,
                width_chars=11,
            ),
            Column("", CellKind.CHEVRON, lambda row: None),
        )

    def _is_absent(self, row: _Involved) -> bool:
        """Return whether the displayed frame lacks the object: by its history, else by the displayed scene, where
        a frame without a scene shows no objects."""
        if self._frame is None:
            return False
        if row.history is not None:
            return not row.history.is_visible_at(self._frame)
        return self._scene is None or EntityId(row.entity_id) not in self._scene.entities

    def _involved(self, entity_id: str) -> _Involved:
        history = self._history.object(entity_id) if self._history is not None else None
        if history is not None:
            return _Involved(entity_id, history, history.types)
        entity = self._scene.entities.get(EntityId(entity_id)) if self._scene is not None else None
        return _Involved(entity_id, None, (entity_summary(entity).class_name,) if entity is not None else ())

    def _request_refresh(self) -> None:
        self._dirty = True
        if self.isVisible():
            self._refresh()

    def _refresh(self) -> None:
        self._dirty = False
        has_history = self._history is not None
        self._go_button.setVisible(has_history)
        self._objects.setColumnHidden(2, not has_history)
        self._objects.setColumnHidden(3, not has_history)
        ids = self._unique_ids()
        self._objects_model.set_rows([self._involved(entity_id) for entity_id in ids], lambda row: row.entity_id)
        self._objects_caption.setVisible(bool(ids))
        self._objects.setVisible(bool(ids))

        if len(self._simultaneous) != self._moment_count:
            self._moment_count = len(self._simultaneous)
            fresh = self._caption(_SAME_MOMENT, self._moment_count)
            layout = self.layout()
            assert isinstance(layout, QVBoxLayout)
            layout.replaceWidget(self._moment_caption, fresh)
            self._moment_caption.deleteLater()
            self._moment_caption = fresh
        self._moment_model.set_rows(self._simultaneous, lambda key: key)
        self._moment_caption.setVisible(bool(self._simultaneous))
        self._moment.setVisible(bool(self._simultaneous))
        self._fit_tables()

    def _fit_tables(self) -> None:
        """Let each table take only the height of its rows, so a short list leaves no empty rows below it."""
        for table, model in ((self._objects, self._objects_model), (self._moment, self._moment_model)):
            lines = model.rowCount() + (0 if table.isHeaderHidden() else 1)
            table.setMaximumHeight(lines * Height.ROW.px + 2 * table.frameWidth())
