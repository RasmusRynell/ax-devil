"""Presence: where an object is shown in the video's length, painted as a strip for list rows and pages."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QSizePolicy, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import category_color
from ax_devil.modules.chrome.tokens import Height, Space
from ax_devil.modules.data_sources.scene_history import FrameSpan

_GROOVE_INSET = Space.S + Space.XS
_TICK_ALPHA = 0.7
_MARKER_PX = 2


@dataclass(frozen=True, slots=True)
class Presence:
    """What a presence strip shows: the frame *spans* (first and last frame, inclusive) an object appears in, out of
    *frame_count* frames, the frames of its events, the displayed frame, and the *category* (its class) that colors it.
    """

    spans: Sequence[FrameSpan]
    frame_count: int
    event_frames: Sequence[int] = ()
    current_frame: int | None = None
    category: str = ""


def paint_presence(
    painter: QPainter,
    rect: QRect,
    palette: QPalette,
    *,
    spans: Sequence[FrameSpan],
    frame_count: int,
    event_frames: Sequence[int],
    current_frame: int | None,
    color: QColor,
) -> None:
    """Draw the video's length across *rect* as a groove with *spans* filled in *color*, a tick for each event frame
    and a marker at *current_frame*.

    Spans that land on the same pixels merge, so the cost follows the strip's width, not the number of spans.
    """
    count = max(frame_count, 1)
    left, width = rect.left(), rect.width()
    if width <= 0:
        return

    def x_of(frame: int) -> int:
        return left + min(max(frame, 0), count) * width // count

    inset = min(_GROOVE_INSET, rect.height() // 3)
    groove_top, groove_height = rect.top() + inset, max(1, rect.height() - 2 * inset)
    painter.fillRect(QRect(left, groove_top, width, groove_height), palette.color(QPalette.ColorRole.Mid))
    run_left = run_right = -1
    for first, last in spans:
        span_left = x_of(first)
        span_right = max(span_left + 1, x_of(last + 1))
        if span_left > run_right:
            if run_right >= 0:
                painter.fillRect(QRect(run_left, groove_top, run_right - run_left, groove_height), color)
            run_left = span_left
        run_right = max(run_right, span_right)
    if run_right >= 0:
        painter.fillRect(QRect(run_left, groove_top, run_right - run_left, groove_height), color)
    tick = QColor(palette.color(QPalette.ColorRole.Text))
    tick.setAlphaF(_TICK_ALPHA)
    for frame in event_frames:
        painter.fillRect(QRect(min(x_of(frame), left + width - 1), rect.top(), 1, rect.height()), tick)
    if current_frame is not None:
        marker_left = min(x_of(current_frame), left + width - _MARKER_PX)
        painter.fillRect(
            QRect(marker_left, rect.top(), _MARKER_PX, rect.height()), palette.color(QPalette.ColorRole.Highlight)
        )


class PresenceStrip(QWidget):
    """A ``Presence`` painted as a strip one row high; a click asks for the frame under the pointer."""

    frameRequested = Signal(int)  # video frame index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presence = Presence(spans=(), frame_count=1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Where the object is shown; ticks mark its events. Click to jump there.")
        follow_appearance(self, lambda: self.setFixedHeight(Height.ROW.px))

    def set_presence(self, presence: Presence) -> None:
        """Show *presence*."""
        self._presence = presence
        self.update()

    def set_current_frame(self, frame: int | None) -> None:
        """Move the displayed-frame marker; ``None`` removes it."""
        if frame != self._presence.current_frame:
            self._presence = replace(self._presence, current_frame=frame)
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return a row-high strip with room to tell frames apart."""
        return QSize(Height.ROW.px * 8, Height.ROW.px)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Paint the strip across the whole widget."""
        presence = self._presence
        painter = QPainter(self)
        paint_presence(
            painter,
            self.rect(),
            self.palette(),
            spans=presence.spans,
            frame_count=presence.frame_count,
            event_frames=presence.event_frames,
            current_frame=presence.current_frame,
            color=category_color(presence.category, self.palette()),
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Request the frame under the pointer."""
        if event.button() != Qt.MouseButton.LeftButton or self.width() <= 0:
            super().mousePressEvent(event)
            return
        count = max(self._presence.frame_count, 1)
        frame = int(event.position().x() * count / self.width())
        self.frameRequested.emit(min(max(frame, 0), count - 1))
