"""Object history pane: where selected objects appear in the video and their data on the displayed frame."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPaintEvent, QPalette, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.tokens import Radius, Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.scene.model import Entity, EntityId, Scene

from .entity_list_widget import entity_detail_html, type_color
from .frame_labels import frame_link

_STRIP_HEIGHT = 16


class PresenceStrip(QWidget):
    """The video's length with one object's appearances, its events and the displayed frame; click to seek."""

    frameRequested = Signal(int)  # video frame index

    def __init__(
        self,
        history: ObjectHistory,
        events: Sequence[FrameEvent],
        frame_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._history = history
        self._event_frames = tuple(event.frame_id.sequence_id for event in events)
        self._frame_count = max(frame_count, 1)
        self._current_frame: int | None = None
        self._color = QColor(type_color(history.types[0] if history.types else ""))
        self.setFixedHeight(_STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Where the object is shown; ticks mark its events. Click to jump there.")

    def set_current_frame(self, frame: int) -> None:
        """Move the displayed-frame marker."""
        if frame != self._current_frame:
            self._current_frame = frame
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Paint the groove, the object's spans, event ticks and the displayed frame."""
        painter = QPainter(self)
        height = float(self.height())
        groove = QRectF(0.0, 3.0, float(self.width()), height - 6.0)
        palette = self.palette()
        painter.fillRect(groove, palette.brush(QPalette.ColorRole.Mid))
        for first, last in self._history.spans:
            left = self._x(first)
            right = self._x(last + 1)
            painter.fillRect(QRectF(left, groove.top(), max(1.0, right - left), groove.height()), self._color)
        for frame in self._event_frames:
            painter.fillRect(QRectF(self._x(frame), 0.0, 1.0, height), palette.brush(QPalette.ColorRole.Text))
        if self._current_frame is not None:
            painter.fillRect(
                QRectF(self._x(self._current_frame), 0.0, 2.0, height), palette.brush(QPalette.ColorRole.Link)
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Request the frame under the pointer."""
        if event.button() != Qt.MouseButton.LeftButton or self.width() <= 0:
            super().mousePressEvent(event)
            return
        frame = int(event.position().x() / self.width() * self._frame_count)
        self.frameRequested.emit(min(max(frame, 0), self._frame_count - 1))

    def _x(self, frame: int) -> float:
        return frame * self.width() / self._frame_count


class ObjectCard(QFrame):
    """One object: a button that opens its data on the displayed frame, and where it is shown in the video."""

    frameRequested = Signal(int)  # video frame index

    def __init__(self, entity_id: str, history: SceneHistory, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("objectCard")
        self.setStyleSheet(f"#objectCard {{ background: palette(alternate-base); border-radius: {Radius.CONTROL}px; }}")
        self._entity_id = entity_id
        self._object_history = history.object(entity_id)
        self._scene: Scene | None = None
        self._frame: int | None = None
        self._shown_entity: Entity | None = None
        self._shown_message = ""
        types = self._object_history.types if self._object_history is not None else ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.S, Space.S, Space.M, Space.S)
        layout.setSpacing(Space.S)
        self._button = QToolButton(self)
        self._button.setObjectName("objectButton")
        self._button.setCheckable(True)
        self._button.setAutoRaise(True)
        self._button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._button.setIcon(_dot_icon(types[0] if types else ""))
        self._button.setToolTip("Show this object's data on the displayed frame")
        self._button_text = f"{entity_id}   {', '.join(types)}".rstrip()
        self._button.setText(f"▸ {self._button_text}")
        self._button.toggled.connect(self._toggle_details)
        layout.addWidget(self._button)

        self._strip: PresenceStrip | None = None
        if self._object_history is None:
            layout.addWidget(self._label("Not shown in this video"))
        else:
            self._strip = PresenceStrip(
                self._object_history, history.events_involving(entity_id), history.frame_count, self
            )
            self._strip.frameRequested.connect(self.frameRequested.emit)
            layout.addWidget(self._strip)
        self._details = self._label("")
        self._details.hide()
        layout.addWidget(self._details)

    def show_frame(self, scene: Scene | None, frame: int) -> None:
        """Follow the displayed frame and the Scene shown on it."""
        self._scene, self._frame = scene, frame
        if self._strip is not None:
            self._strip.set_current_frame(frame)
        self._refresh_details()

    def _toggle_details(self, shown: bool) -> None:
        self._button.setText(f"{'▾' if shown else '▸'} {self._button_text}")
        self._details.setVisible(shown)
        self._refresh_details()

    def _refresh_details(self) -> None:
        """Show the object as displayed on the current frame, or why it is not."""
        if not self._button.isChecked():
            return
        entity = self._scene.entities.get(EntityId(self._entity_id)) if self._scene is not None else None
        if entity is not None:
            if entity is self._shown_entity:
                return
            self._shown_entity, self._shown_message = entity, ""
            self._details.setText(entity_detail_html(entity))
            return
        message = self._absence_message()
        if self._shown_entity is None and message == self._shown_message:
            return
        self._shown_entity, self._shown_message = None, message
        self._details.setText(escape(message))

    def _absence_message(self) -> str:
        history, frame = self._object_history, self._frame
        if history is not None and frame is not None and history.is_visible_at(frame):
            return "In this frame, but not shown (check the filters)"
        return "Not in this frame"

    def _label(self, html: str) -> QLabel:
        label = QLabel(html, self)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        TextRole.SMALL.apply(label)
        return label


class ObjectHistoryPane(QWidget):
    """Cards for the selected object, or for every object of a selected event."""

    frameRequested = Signal(int)  # video frame index

    def __init__(self, history: SceneHistory, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("objectHistoryPane")
        self._history = history
        self._cards: list[ObjectCard] = []
        self._selection: tuple[str, tuple[str, ...]] | None = None
        self._scene: Scene | None = None
        self._current_frame: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, Space.M, Space.M, 0)  # Matches the media tools tab pages above
        layout.setSpacing(Space.S)
        header = QHBoxLayout()
        self._title = QLabel("", self)
        self._title.setTextFormat(Qt.TextFormat.RichText)
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._title.linkActivated.connect(lambda href: self.frameRequested.emit(int(href)))
        header.addWidget(self._title, 1)
        close_button = QToolButton(self)
        close_button.setText("×")
        close_button.setAutoRaise(True)
        close_button.setToolTip("Close")
        close_button.clicked.connect(self.clear)
        header.addWidget(close_button)
        layout.addLayout(header)

        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(Space.S)
        self._cards_layout.addStretch(1)
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(self._cards_widget)
        layout.addWidget(scroll_area, 1)
        self.hide()

    def set_history(self, history: SceneHistory) -> None:
        """Rebuild the shown cards from a replacement history."""
        self._history = history
        if self._selection is not None:
            self._show(*self._selection)

    def show_object(self, entity_id: str) -> None:
        """Show the history of one object."""
        self._show("<b>Object</b>", (entity_id,))

    def show_event(self, event: FrameEvent) -> None:
        """Show an event and the history of every object it involves."""
        self._show(f"<b>{escape(event.label)}</b> {frame_link(event.frame_id)}", event.entity_ids)

    def show_frame(self, scene: Scene | None, frame: int) -> None:
        """Let every card follow the displayed frame and the Scene shown on it, once the pane is shown."""
        self._scene, self._current_frame = scene, frame
        if self.isVisible():
            self._update_cards()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Catch the cards up with the displayed frame."""
        super().showEvent(event)
        self._update_cards()

    def _update_cards(self) -> None:
        if self._current_frame is None:
            return
        for card in self._cards:
            card.show_frame(self._scene, self._current_frame)

    def clear(self) -> None:
        """Remove the cards and hide the pane."""
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards = []
        self._selection = None
        self.hide()

    def _show(self, title_html: str, entity_ids: Sequence[str]) -> None:
        self.clear()
        self._selection = (title_html, tuple(entity_ids))
        self._title.setText(title_html)
        for entity_id in entity_ids:
            card = ObjectCard(entity_id, self._history, self._cards_widget)
            card.frameRequested.connect(self.frameRequested.emit)
            self._cards_layout.insertWidget(len(self._cards), card)
            self._cards.append(card)
        self.show()
        if self.isVisible():
            self._update_cards()


def _dot_icon(object_type: str) -> QIcon:
    pixmap = QPixmap(10, 10)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(type_color(object_type)))
    painter.drawEllipse(1, 1, 8, 8)
    painter.end()
    return QIcon(pixmap)
