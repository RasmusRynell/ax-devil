"""Object inspection over video: complete grouped data, scrollable and selectable when pinned."""

from __future__ import annotations

from math import ceil

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QTextOption
from PySide6.QtWidgets import QFrame, QTextBrowser, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole

_CARD_STYLE = f"""
QFrame#EntityHoverCard {{
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: {Radius.POPUP}px;
}}
QFrame#EntityHoverCard QTextBrowser {{
    background: transparent;
    border: none;
}}
"""
_ANCHOR_OFFSET = 14
_CARD_PADDING = Space.S
_MIN_TEXT_CHARACTERS = 24
_MAX_TEXT_CHARACTERS = 56
_MIN_TEXT_LINES = 3


class EntityHoverCard(QFrame):
    """Floating card shown when hovering over an entity bounding box."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("EntityHoverCard")
        self.setStyleSheet(_CARD_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Clicks, selection drags and wheel on a pinned card must not unpin, re-pin or zoom the video beneath it.
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # The document margin pads the card, so the whole card scrolls and selects text.
        self._browser = QTextBrowser(self)
        self._browser.setFrameShape(QFrame.Shape.NoFrame)
        self._browser.setOpenLinks(False)
        self._browser.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._browser.document().setDocumentMargin(Space.M)
        layout.addWidget(self._browser)

        self._current_target_id: str | None = None
        self._current_card_html: str | None = None
        self._placement: tuple[int, int, tuple[int, int, int, int] | None] | None = None
        self._fitted: tuple[QSize, QFont, str] | None = None
        follow_appearance(self._browser, self._apply_appearance)
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
        interactive: bool = False,
    ) -> None:
        """Show complete inspection data; only pinned cards capture input."""
        same_target = target_id == self._current_target_id
        self._current_target_id = target_id
        self._current_card_html = card_html
        self._placement = (anchor_x, anchor_y, avoid_rect)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)
        self._fit(same_target=same_target)
        self.show()
        self.raise_()

    def hide_card(self) -> None:
        """Hide the card."""
        self._current_target_id = None
        self._current_card_html = None
        self._placement = None
        self._fitted = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_appearance(self) -> None:
        self._browser.setFont(TextRole.SMALL.font())
        self._fit(same_target=True)

    def _fit(self, *, same_target: bool) -> None:
        """Size the card to its text within the viewer, keeping scroll and size steady for the same object."""
        parent = self.parentWidget()
        if parent is None or self._current_card_html is None or self._placement is None:
            return
        fitted = (parent.size(), self._browser.font(), self._current_card_html)
        if fitted != self._fitted:
            content_changed = self._fitted is None or self._current_card_html != self._fitted[2]
            self._fitted = fitted
            scroll = self._browser.verticalScrollBar()
            scroll_position = scroll.value() if same_target else 0
            self.ensurePolished()
            self._browser.ensurePolished()
            frame = 2 * (self.frameWidth() + self._browser.frameWidth())
            metrics = QFontMetrics(self._browser.font())
            character_width = metrics.averageCharWidth()
            max_width = max(1, min(parent.width() - 2 * _CARD_PADDING - frame, _MAX_TEXT_CHARACTERS * character_width))
            document_padding = 2 * Space.M
            max_height = max(1, parent.height() - 2 * _CARD_PADDING - frame)
            min_width = min(max_width, _MIN_TEXT_CHARACTERS * character_width)
            min_height = min(max_height, _MIN_TEXT_LINES * metrics.lineSpacing() + document_padding)
            document = self._browser.document()
            # Lay the shown text out once, at its final width, while values change every frame.
            document.setLayoutEnabled(False)
            try:
                if content_changed:
                    self._browser.setHtml(self._current_card_html)
                # QTextEdit pins its document to the viewport width, so measure a copy.
                measure = document.clone(self)
                measure.setDocumentMargin(document.documentMargin())
                measure.setTextWidth(max_width)
                width = min(max_width, max(min_width, ceil(measure.idealWidth())))
                if same_target:
                    width = min(max_width, max(width, self.width() - frame))
                if width < max_width:
                    measure.setTextWidth(width)
                height = min(max_height, max(min_height, ceil(measure.size().height())))
                measure.deleteLater()
                if same_target:
                    height = min(max_height, max(height, self.height() - frame))
                self.setFixedSize(width + frame, height + frame)
            finally:
                document.setLayoutEnabled(True)
            scroll.setValue(scroll_position)
        self._reposition(*self._placement)

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
