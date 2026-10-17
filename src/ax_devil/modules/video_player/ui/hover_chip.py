"""A compact label beside the object under the pointer, painted on a dark scrim like the playback controls."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import VIDEO_CANVAS, VIDEO_CANVAS_TEXT, category_color
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole
from ax_devil.modules.video_player.engine.data_types import HoverHit

_SCRIM = QColor(VIDEO_CANVAS.red(), VIDEO_CANVAS.green(), VIDEO_CANVAS.blue(), 230)
_BORDER = QColor(255, 255, 255, 40)
_ANCHOR_OFFSET = Space.M
_CHIP_PADDING = Space.S
_MAX_LABEL_CHARACTERS = 40


def _video_palette() -> QPalette:
    """Return the palette of the video surface, which is dark in every theme, for picking category colors."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    return palette


class HoverChip(QWidget):
    """Non-interactive chip naming the object under the pointer: its color dot, label and detail on one line."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._hit: HoverHit | None = None
        self._placement: tuple[int, int, tuple[int, int, int, int] | None] | None = None
        self._label = ""
        self._detail = ""
        self._label_x = 0
        self._detail_x = 0
        self._dot = 0
        follow_appearance(self, self._refit)
        self.hide()

    def show_for(
        self,
        hit: HoverHit,
        anchor_x: int,
        anchor_y: int,
        *,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        """Show the chip for *hit* near the anchor point, keeping off *avoid_rect* when there is room."""
        self._hit = hit
        self._placement = (anchor_x, anchor_y, avoid_rect)
        self._refit()
        self.show()
        self.raise_()

    def hide_chip(self) -> None:
        """Hide the chip."""
        self._hit = None
        self._placement = None
        self.hide()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        """Draw the scrim, the category dot, the label and the detail."""
        hit = self._hit
        if hit is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(_BORDER)
        painter.setBrush(_SCRIM)
        painter.drawRoundedRect(outline, Radius.CONTROL, Radius.CONTROL)

        label_font, detail_font = TextRole.MONO.font(), TextRole.SMALL.font()
        label_metrics, detail_metrics = QFontMetrics(label_font), QFontMetrics(detail_font)
        ascent = max(label_metrics.ascent(), detail_metrics.ascent())
        descent = max(label_metrics.descent(), detail_metrics.descent())
        baseline = (self.height() - ascent - descent) // 2 + ascent

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(category_color(hit.color_key, _video_palette()))
        painter.drawEllipse(QRectF(_CHIP_PADDING + Space.XS, (self.height() - self._dot) / 2, self._dot, self._dot))
        painter.setFont(label_font)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(QPoint(self._label_x, baseline), self._label)
        if self._detail:
            painter.setFont(detail_font)
            painter.setPen(VIDEO_CANVAS_TEXT)
            painter.drawText(QPoint(self._detail_x, baseline), self._detail)

    def _refit(self) -> None:
        """Size the chip to its text, eliding label and detail when the viewer is too narrow, and place it."""
        parent = self.parentWidget()
        if parent is None or self._hit is None or self._placement is None:
            return
        label_metrics, detail_metrics = QFontMetrics(TextRole.MONO.font()), QFontMetrics(TextRole.SMALL.font())
        self._dot = label_metrics.ascent() // 2 + Space.XS
        self._label_x = _CHIP_PADDING + Space.XS + self._dot + Space.S
        text_room = parent.width() - 2 * _CHIP_PADDING - self._label_x - _CHIP_PADDING - Space.XS
        detail_width = detail_metrics.horizontalAdvance(self._hit.detail)
        # The label yields room to the detail only down to half of what is left.
        label_room = min(
            max(text_room - detail_width - Space.M, text_room // 2),
            _MAX_LABEL_CHARACTERS * label_metrics.averageCharWidth(),
        )
        self._label = label_metrics.elidedText(self._hit.label, Qt.TextElideMode.ElideMiddle, max(0, label_room))
        label_width = label_metrics.horizontalAdvance(self._label)
        self._detail_x = self._label_x + label_width + Space.M
        detail_room = text_room - label_width - Space.M
        self._detail = detail_metrics.elidedText(self._hit.detail, Qt.TextElideMode.ElideRight, max(0, detail_room))
        text_end = (
            self._detail_x + detail_metrics.horizontalAdvance(self._detail)
            if self._detail
            else self._detail_x - Space.M
        )
        height = max(label_metrics.height(), detail_metrics.height()) + 2 * _CHIP_PADDING
        self.setFixedSize(text_end + _CHIP_PADDING + Space.XS, height)
        self.update()
        self._reposition(*self._placement)

    def _reposition(
        self,
        anchor_x: int,
        anchor_y: int,
        avoid_rect: tuple[int, int, int, int] | None,
    ) -> None:
        """Place the chip near the anchor point, keeping it inside the parent widget."""
        parent = self.parentWidget()
        if parent is None:
            self.move(anchor_x + _ANCHOR_OFFSET, anchor_y + _ANCHOR_OFFSET)
            return

        pw, ph = parent.width(), parent.height()
        cw, ch = self.width(), self.height()

        if avoid_rect is not None:
            placement = self._position_avoiding_rect(anchor_x, anchor_y, cw, ch, pw, ph, avoid_rect)
            if placement is not None:
                self.move(*placement)
                return

        x = self._position_along_axis(anchor_x, cw, pw)
        y = self._position_along_axis(anchor_y, ch, ph)
        self.move(x, y)

    def _position_avoiding_rect(
        self,
        anchor_x: int,
        anchor_y: int,
        chip_width: int,
        chip_height: int,
        parent_width: int,
        parent_height: int,
        avoid_rect: tuple[int, int, int, int],
    ) -> tuple[int, int] | None:
        """Return a non-overlapping placement when one fits inside the parent."""
        avoid_x, avoid_y, avoid_width, avoid_height = avoid_rect
        avoid_right = avoid_x + avoid_width
        avoid_bottom = avoid_y + avoid_height
        vertical_anchor = self._clamp_position(anchor_y, chip_height, parent_height)
        horizontal_anchor = self._clamp_position(anchor_x - chip_width, chip_width, parent_width)

        candidates = sorted(
            [
                (parent_width - avoid_right - _CHIP_PADDING, 0, avoid_right + _ANCHOR_OFFSET, vertical_anchor),
                (avoid_x - _CHIP_PADDING, 1, avoid_x - chip_width - _ANCHOR_OFFSET, vertical_anchor),
                (avoid_y - _CHIP_PADDING, 2, horizontal_anchor, avoid_y - chip_height - _ANCHOR_OFFSET),
                (parent_height - avoid_bottom - _CHIP_PADDING, 3, horizontal_anchor, avoid_bottom + _ANCHOR_OFFSET),
            ],
            key=lambda candidate: (-candidate[0], candidate[1]),
        )
        for _available_space, _priority, candidate_x, candidate_y in candidates:
            x = self._clamp_position(candidate_x, chip_width, parent_width)
            y = self._clamp_position(candidate_y, chip_height, parent_height)
            if not self._rects_intersect((x, y, chip_width, chip_height), avoid_rect):
                return (x, y)

        return None

    def _position_along_axis(self, anchor: int, chip_size: int, parent_size: int) -> int:
        """Choose the side with more room, then clamp inside the parent."""
        forward_space = parent_size - anchor - _CHIP_PADDING
        backward_space = anchor - _CHIP_PADDING

        if forward_space >= chip_size + _ANCHOR_OFFSET or forward_space >= backward_space:
            position = anchor + _ANCHOR_OFFSET
        else:
            position = anchor - chip_size - _ANCHOR_OFFSET

        return self._clamp_position(position, chip_size, parent_size)

    def _clamp_position(self, position: int, item_size: int, parent_size: int) -> int:
        return max(_CHIP_PADDING, min(position, parent_size - item_size - _CHIP_PADDING))

    def _rects_intersect(
        self,
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> bool:
        first_x, first_y, first_width, first_height = first
        second_x, second_y, second_width, second_height = second
        return not (
            first_x + first_width <= second_x
            or second_x + second_width <= first_x
            or first_y + first_height <= second_y
            or second_y + second_height <= first_y
        )
