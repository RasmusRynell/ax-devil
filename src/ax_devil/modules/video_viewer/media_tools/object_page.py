"""The page of one object: who it is, where it appears in the video, its fields in the frame and its events."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import SEE_THROUGH
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameSpan, ObjectHistory, SceneHistory
from ax_devil.modules.object_inspection.fields_view import FieldsView, Section, event_section, field_sections, id_row
from ax_devil.modules.object_inspection.frame_labels import format_frame_time
from ax_devil.modules.object_inspection.title_row import TitleRow
from ax_devil.modules.scene.inspection import entity_summary
from ax_devil.modules.scene.model import EntityId, Scene
from ax_devil.modules.video_viewer.media_tools.presence import Presence, PresenceStrip

_US_PER_SECOND = 1_000_000
_ATTRIBUTES_NOTE = "Attributes are only available while an object is in the frame."
_MESSAGE_STYLE = "color: palette(placeholder-text); border: none;"


@dataclass(frozen=True, slots=True)
class _Absence:
    """What to tell about an object that the displayed frame does not show: the pill, the message and where it is
    seen before and after the frame, if anywhere."""

    pill: str
    message: str
    last_seen: int | None = None
    next_seen: int | None = None


class ObjectPage(QWidget):
    """One object by id: a title, where it appears in the video, its fields and the events involving it.

    The fields come from the object in the displayed Scene, so they are shown only while it is in the frame; out of
    the frame the page says when it was last seen and offers to go there. Without a ``SceneHistory`` (a live stream)
    there is no presence strip and no events. The content is built when the page is first shown, and a page that is
    not shown keeps only the latest frame and catches up when it is.

    *fold_state* maps section names to whether they are folded; pages share it so a fold carries over to the next
    object.
    """

    frameRequested = Signal(int)  # video frame index
    eventActivated = Signal(object)  # FrameEvent

    def __init__(self, entity_id: str, fold_state: dict[str, bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entity_id = entity_id
        self._fold_state = fold_state
        self._history: SceneHistory | None = None
        self._events_section: Section | None = None
        self._events_loaded = False
        self._event_frames: tuple[int, ...] = ()
        self._scene: Scene | None = None
        self._frame: int | None = None
        self._last_class = ""
        self._last_seen: int | None = None
        self._next_seen: int | None = None
        self._built = False
        self._dirty = True
        self.setStyleSheet(SEE_THROUGH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    @property
    def entity_id(self) -> str:
        """Return the id of the object this page shows."""
        return self._entity_id

    def set_history(self, history: SceneHistory | None) -> None:
        """Show where the object appears and its events from *history*; ``None`` (a live stream) shows neither."""
        self._history = history
        self._events_loaded = False
        self._request_refresh()

    def update_scene(self, scene: Scene | None, frame_index: int) -> None:
        """Follow the displayed *frame_index* and its *scene*, which has the object while it is in the frame."""
        self._scene, self._frame = scene, frame_index
        self._request_refresh()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Build the content on first showing, and catch up with the latest frame."""
        super().showEvent(event)
        if not self._built:
            self._build()
        if self._dirty:
            self._refresh()

    def _request_refresh(self) -> None:
        self._dirty = True
        if self.isVisible():
            self._refresh()

    def _build(self) -> None:
        self._built = True
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        self._title = TitleRow(self)
        layout.addWidget(self._title)

        self._strip = PresenceStrip(self)
        self._strip.frameRequested.connect(self.frameRequested)
        self._strip_box = self._boxed(self._strip, layout)

        self._message = QLabel(self)
        self._message.setWordWrap(True)
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        TextRole.SMALL.apply(self._message)
        self._message.setStyleSheet(_MESSAGE_STYLE)
        self._last_button = SmallButton("Go to last seen", parent=self)
        self._last_button.clicked.connect(lambda: self._request_frame(self._last_seen))
        self._next_button = SmallButton("Next appearance", parent=self)
        self._next_button.clicked.connect(lambda: self._request_frame(self._next_seen))
        self._absence_box = QWidget(self)
        absence_layout = QVBoxLayout(self._absence_box)
        absence_layout.setContentsMargins(Space.M, Space.S, Space.M, Space.S)
        absence_layout.setSpacing(Space.S)
        absence_layout.addWidget(self._message)
        buttons = QHBoxLayout()
        buttons.setSpacing(Space.S)
        buttons.addWidget(self._last_button)
        buttons.addWidget(self._next_button)
        buttons.addStretch(1)
        absence_layout.addLayout(buttons)
        layout.addWidget(self._absence_box)

        self._fields = FieldsView(self._fold_state, self)
        self._fields.eventActivated.connect(self.eventActivated)
        self.setFocusProxy(self._fields)  # The page gets focus as its fields view, so Ctrl+C copies without a click.
        layout.addWidget(self._fields, 1)

    def _boxed(self, widget: QWidget, layout: QVBoxLayout) -> QWidget:
        box = QWidget(self)
        box_layout = QHBoxLayout(box)
        box_layout.setContentsMargins(Space.M, Space.S, Space.M, Space.S)
        box_layout.addWidget(widget)
        layout.addWidget(box)
        return box

    def _request_frame(self, frame: int | None) -> None:
        if frame is not None:
            self.frameRequested.emit(frame)

    def _load_events(self) -> None:
        """Collect the events involving the object from the current history, once per history and only when shown."""
        self._events_loaded = True
        events = self._history.events_involving(self._entity_id) if self._history is not None else ()
        self._events_section = event_section(events) if self._history is not None else None
        self._event_frames = tuple(event.frame_id.sequence_id for event in events)

    def _refresh(self) -> None:
        self._dirty = False
        if not self._events_loaded:
            self._load_events()
        frame = self._frame
        if frame is None:
            return
        entity = self._scene.entities.get(EntityId(self._entity_id)) if self._scene is not None else None
        history = self._history.object(self._entity_id) if self._history is not None else None
        if entity is not None:
            summary = entity_summary(entity)
            self._last_class = summary.class_name
            sections = list(field_sections(entity))
            self._title.set_content(summary.class_name, summary.state or "", summary.state, dimmed=False)
            absence = None
        else:
            absence = self._absence(history, frame)
            sections = []
            if history is not None and history.types:
                self._last_class = history.types[0]
            self._title.set_content(self._last_class, absence.pill, None, dimmed=True)
        if self._events_section is not None:
            sections.append(self._events_section)
        self._fields.set_sections(sections, (id_row(self._entity_id),))

        self._strip_box.setVisible(history is not None)
        if history is not None and self._history is not None:
            self._strip.set_presence(
                Presence(
                    history.spans,
                    self._history.frame_count,
                    self._event_frames,
                    frame,
                    self._last_class,
                )
            )
        self._absence_box.setVisible(absence is not None)
        if absence is not None:
            self._last_seen, self._next_seen = absence.last_seen, absence.next_seen
            self._message.setText(absence.message)
            self._last_button.setVisible(absence.last_seen is not None)
            self._next_button.setVisible(absence.next_seen is not None)

    @staticmethod
    def _absence(history: ObjectHistory | None, frame: int) -> _Absence:
        """Describe why *frame* does not show the object, and where it is seen before and after *frame*."""
        if history is None:
            return _Absence("not in this frame", f"Not in the current frame. {_ATTRIBUTES_NOTE}")
        spans: tuple[FrameSpan, ...] = history.spans
        started = bisect_right(spans, frame, key=lambda span: span[0])
        if started and frame <= spans[started - 1][1]:
            return _Absence(
                "hidden by filter",
                "In this frame, but hidden by the overlay filter. "
                "Attributes are only available for objects that pass it.",
            )
        times = history.frame_times_us
        now = times[min(frame, len(times) - 1)]
        last = spans[started - 1][1] if started else None
        upcoming = spans[started][0] if started < len(spans) else None
        if last is not None:
            when = f"Last seen at {format_frame_time(times[last])} ({(now - times[last]) / _US_PER_SECOND:.1f} s ago)"
        elif upcoming is not None:
            when = (
                f"First seen at {format_frame_time(times[upcoming])} "
                f"(in {(times[upcoming] - now) / _US_PER_SECOND:.1f} s)"
            )
        else:
            when = "Not seen"
        return _Absence("not in this frame", f"{when}. {_ATTRIBUTES_NOTE}", last, upcoming)
