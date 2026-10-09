"""Object inspection over video: complete grouped data, scrollable and selectable when pinned."""

from __future__ import annotations

from math import ceil

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QTextOption, QWheelEvent
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


class _InspectionBrowser(QTextBrowser):
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        super().wheelEvent(event)
        event.accept()


class EntityHoverCard(QFrame):
    """Floating card shown when hovering over an entity bounding box."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("EntityHoverCard")
        self.setStyleSheet(_CARD_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        layout.setSpacing(0)

        self._browser = _InspectionBrowser(self)
        self._browser.setFrameShape(QFrame.Shape.NoFrame)
        self._browser.setOpenLinks(False)
        self._browser.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._browser.document().setDocumentMargin(0)
        layout.addWidget(self._browser)

        self._current_target_id: str | None = None
        self._current_card_html: str | None = None
        self._sections: tuple[str, ...] = ()
        self._layout_key: tuple[int, int, str, tuple[str, ...]] | None = None
        self._column_count = 1
        self._placement: tuple[int, int, tuple[int, int, int, int] | None] | None = None
        follow_appearance(self, self._apply_appearance)
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
        sections: tuple[str, ...] = (),
        interactive: bool = False,
    ) -> None:
        """Show complete inspection data; only pinned cards capture input."""
        new_target = target_id != self._current_target_id
        self._current_target_id = target_id
        self._current_card_html = card_html
        self._sections = sections
        self._placement = (anchor_x, anchor_y, avoid_rect)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)
        if new_target:
            self._layout_key = None
        self._update_layout(new_target=new_target)
        self._reposition(anchor_x, anchor_y, avoid_rect)
        self.show()
        self.raise_()

    def hide_card(self) -> None:
        """Hide the card."""
        self._current_target_id = None
        self._current_card_html = None
        self._sections = ()
        self._layout_key = None
        self._placement = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Scroll from the card padding as well as the text, without zooming the video."""
        self._browser.wheelEvent(event)

    def _apply_appearance(self) -> None:
        TextRole.SMALL.apply(self._browser)
        self._browser.document().setDefaultFont(self._browser.font())
        self._layout_key = None
        if self._current_card_html is not None:
            self._update_layout()
            if self._placement is not None:
                self._reposition(*self._placement)

    def _update_layout(self, *, new_target: bool = False) -> None:
        parent = self.parentWidget()
        if parent is None or self._current_card_html is None:
            return
        key = (parent.width(), parent.height(), self._current_card_html, self._sections)
        if key == self._layout_key:
            return
        self._layout_key = key

        margins = 2 * (Space.M + self.frameWidth())
        available_width = max(1, parent.width() - 2 * _CARD_PADDING - margins)
        available_height = max(1, parent.height() - 2 * _CARD_PADDING - margins)
        character_width = QFontMetrics(self._browser.font()).averageCharWidth()
        self._column_count = 2 if len(self._sections) > 1 and available_width >= 100 * character_width else 1
        width = min(available_width, (104 if self._column_count == 2 else 56) * character_width)
        scroll = self._browser.verticalScrollBar()
        scroll_position = 0 if new_target else scroll.value()
        html = self._current_card_html
        if self._sections:
            split = (len(self._sections) + self._column_count - 1) // self._column_count
            columns = [self._sections[index : index + split] for index in range(0, len(self._sections), split)]
            cells = "".join(
                f'<td width="{100 // self._column_count}%" valign="top">{"<br/>".join(column)}</td>'
                for column in columns
            )
            html = f'{html}<br/><table width="100%" cellspacing="{Space.M}" cellpadding="0"><tr>{cells}</tr></table>'
        document = self._browser.document()
        document.setLayoutEnabled(False)
        try:
            self._browser.setHtml(html)
            self._browser.setFixedWidth(width)
            document.setTextWidth(self._browser.viewport().width())
        finally:
            document.setLayoutEnabled(True)
        if not self._sections:
            document.setTextWidth(-1)
            width = min(width, ceil(document.idealWidth()) + Space.XS)
            self._browser.setFixedWidth(width)
            document.setTextWidth(self._browser.viewport().width())
        height = min(available_height, ceil(document.size().height()) + 2 * self._browser.frameWidth())
        if not new_target:
            height = min(available_height, max(height, self._browser.height()))
        self._browser.setFixedHeight(height)
        self.setFixedSize(width + margins, height + margins)
        scroll.setValue(scroll_position)

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
