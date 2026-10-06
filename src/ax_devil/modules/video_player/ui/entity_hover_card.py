"""Hover card that displays entity data overlaid on the video widget.

Uses a single HTML QLabel for content — no dynamic widget creation/deletion,
which avoids all deleteLater / layout-residue bugs on rapid entity transitions.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

_CARD_STYLE = """
QFrame#EntityHoverCard {
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: 8px;
}
QFrame#EntityHoverCard QLabel {
    background: transparent;
}
"""
_ANCHOR_OFFSET = 14
_CARD_PADDING = 4


class EntityHoverCard(QFrame):
    """Floating card shown when hovering over an entity bounding box."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("EntityHoverCard")
        self.setStyleSheet(_CARD_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(0)

        self._label = QLabel()
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setWordWrap(False)
        layout.addWidget(self._label)

        self._current_target_id: str | None = None
        self._current_card_html: str | None = None
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_for(
        self,
        target_id: str,
        card_html: str,
        anchor_x: int,
        anchor_y: int,
        *,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        """Show card for target id with preformatted html payload."""
        if self._should_update_content(target_id, card_html):
            self._current_target_id = target_id
            self._current_card_html = card_html
            self._label.setText(card_html)
            self.adjustSize()
        self._reposition(anchor_x, anchor_y, avoid_rect)
        self.show()
        self.raise_()

    def hide_card(self) -> None:
        """Hide the card."""
        self._current_target_id = None
        self._current_card_html = None
        self.hide()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _should_update_content(self, target_id: str, card_html: str) -> bool:
        return target_id != self._current_target_id or card_html != self._current_card_html

    def _reposition(
        self,
        anchor_x: int,
        anchor_y: int,
        avoid_rect: tuple[int, int, int, int] | None,
    ) -> None:
        """Place the card near the anchor point, keeping it inside the parent widget."""
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
        card_width: int,
        card_height: int,
        parent_width: int,
        parent_height: int,
        avoid_rect: tuple[int, int, int, int],
    ) -> tuple[int, int] | None:
        """Return a non-overlapping placement when one fits inside the parent."""
        avoid_x, avoid_y, avoid_width, avoid_height = avoid_rect
        avoid_right = avoid_x + avoid_width
        avoid_bottom = avoid_y + avoid_height
        vertical_anchor = self._clamp_position(anchor_y, card_height, parent_height)
        horizontal_anchor = self._clamp_position(anchor_x - card_width, card_width, parent_width)

        candidates = sorted(
            [
                (parent_width - avoid_right - _CARD_PADDING, 0, avoid_right + _ANCHOR_OFFSET, vertical_anchor),
                (avoid_x - _CARD_PADDING, 1, avoid_x - card_width - _ANCHOR_OFFSET, vertical_anchor),
                (avoid_y - _CARD_PADDING, 2, horizontal_anchor, avoid_y - card_height - _ANCHOR_OFFSET),
                (parent_height - avoid_bottom - _CARD_PADDING, 3, horizontal_anchor, avoid_bottom + _ANCHOR_OFFSET),
            ],
            key=lambda candidate: (-candidate[0], candidate[1]),
        )
        for _available_space, _priority, candidate_x, candidate_y in candidates:
            x = self._clamp_position(candidate_x, card_width, parent_width)
            y = self._clamp_position(candidate_y, card_height, parent_height)
            if not self._rects_intersect((x, y, card_width, card_height), avoid_rect):
                return (x, y)

        return None

    def _position_along_axis(self, anchor: int, card_size: int, parent_size: int) -> int:
        """Choose the side with more room, then clamp inside the parent."""
        forward_space = parent_size - anchor - _CARD_PADDING
        backward_space = anchor - _CARD_PADDING

        if forward_space >= card_size + _ANCHOR_OFFSET or forward_space >= backward_space:
            position = anchor + _ANCHOR_OFFSET
        else:
            position = anchor - card_size - _ANCHOR_OFFSET

        return self._clamp_position(position, card_size, parent_size)

    def _clamp_position(self, position: int, item_size: int, parent_size: int) -> int:
        return max(_CARD_PADDING, min(position, parent_size - item_size - _CARD_PADDING))

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
