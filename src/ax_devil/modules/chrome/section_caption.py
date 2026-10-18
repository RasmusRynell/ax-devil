"""The caption that heads a group of rows in a sidebar: a color mark, a title and a rule to the edge."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole

_MARK_PX = 8
_ARROW_PX = Space.L
_RULE_ALPHA = 0.12


def paint_section_caption(
    painter: QPainter, rect: QRect, palette: QPalette, text: str, mark: QColor, aside: str = ""
) -> None:
    """Draw a caption in *rect*: *mark* as a small square, *text*, and a rule from the text to the right edge.

    A non-empty *aside* is drawn muted at the right edge, elided to the room the text leaves, with the rule between.
    """
    rect = rect.adjusted(Space.S, 0, -Space.S, 0)
    center_y = rect.center().y()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(mark)
    painter.drawRoundedRect(
        QRectF(rect.left(), center_y - _MARK_PX / 2 + 1, _MARK_PX, _MARK_PX), Radius.CONTROL / 2, Radius.CONTROL / 2
    )
    font = TextRole.CAPTION.font()
    painter.setFont(font)
    painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
    text_left = rect.left() + _MARK_PX + Space.S
    painter.drawText(
        QRect(text_left, rect.top(), rect.width(), rect.height()),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        text,
    )
    rule_left = text_left + QFontMetrics(font).horizontalAdvance(text) + Space.S
    rule_right = rect.right()
    if aside and rule_left < rect.right():
        aside_font = TextRole.SMALL.font()
        painter.setFont(aside_font)
        shown = QFontMetrics(aside_font).elidedText(aside, Qt.TextElideMode.ElideRight, rect.right() - rule_left)
        painter.drawText(
            QRect(rule_left, rect.top(), rect.right() - rule_left + 1, rect.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            shown,
        )
        rule_right = rect.right() - QFontMetrics(aside_font).horizontalAdvance(shown) - Space.S
    if rule_left < rule_right:
        rule = QColor(palette.color(QPalette.ColorRole.Text))
        rule.setAlphaF(_RULE_ALPHA)
        painter.setPen(rule)
        painter.drawLine(rule_left, center_y + 1, rule_right, center_y + 1)
    painter.restore()


class SectionCaption(QWidget):
    """A caption widget: *title*, with *count* after it when given and then *note*, marked in the color *mark* gives
    for the palette.

    A caption over rows that fold is given *folded*, and leads with the arrow a tree draws beside rows with children,
    in a column *arrow_px* wide.
    """

    def __init__(
        self,
        title: str,
        count: int | None,
        mark: Callable[[QPalette], QColor],
        *,
        note: str = "",
        folded: bool | None = None,
        arrow_px: int = _ARROW_PX,
    ) -> None:
        super().__init__()
        counted = title if count is None else f"{title}  {count}"
        self._text = f"{counted}  · {note}" if note else counted
        self._mark = mark
        self._folded = folded
        self._arrow_px = arrow_px
        self._aside = ""
        if folded is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        follow_appearance(self, lambda: self.setFixedHeight(Height.ROW.px))

    def text(self) -> str:
        """Return the text the caption shows."""
        return self._text

    def set_aside(self, text: str) -> None:
        """Show *text* muted at the right end of the caption, elided to fit; the caption keeps its height when it is
        empty."""
        self._aside = text
        self.update()

    def aside(self) -> str:
        """Return the text shown at the right end of the caption."""
        return self._aside

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        """Draw the caption, after its arrow when it folds."""
        painter = QPainter(self)
        rect = self.rect()
        if self._folded is not None:
            # The style draws the arrow only for a view item's option.
            arrow = QStyleOptionViewItem()
            arrow.initFrom(self)
            arrow.rect = QRect(rect.left(), rect.top(), self._arrow_px, rect.height())
            arrow.state = QStyle.StateFlag.State_Children | QStyle.StateFlag.State_Enabled
            if not self._folded:
                arrow.state |= QStyle.StateFlag.State_Open
            self.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorBranch, arrow, painter, self)
            # The caption keeps its own inset before the mark; the arrow stands in most of it.
            rect = rect.adjusted(self._arrow_px - Space.S, 0, 0, 0)
        paint_section_caption(painter, rect, self.palette(), self._text, self._mark(self.palette()), self._aside)
