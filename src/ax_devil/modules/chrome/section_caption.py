"""The caption that heads a group of rows in a sidebar: a color mark, a title and a rule to the edge."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole

_MARK_PX = 8
_RULE_ALPHA = 0.12


def paint_section_caption(painter: QPainter, rect: QRect, palette: QPalette, text: str, mark: QColor) -> None:
    """Draw a caption in *rect*: *mark* as a small square, *text*, and a rule from the text to the right edge."""
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
    if rule_left < rect.right():
        rule = QColor(palette.color(QPalette.ColorRole.Text))
        rule.setAlphaF(_RULE_ALPHA)
        painter.setPen(rule)
        painter.drawLine(rule_left, center_y + 1, rect.right(), center_y + 1)
    painter.restore()


class SectionCaption(QWidget):
    """A caption widget: *title*, with *count* after it when given, marked in the color *mark* gives for the palette."""

    def __init__(self, title: str, count: int | None, mark: Callable[[QPalette], QColor]) -> None:
        super().__init__()
        self._text = title if count is None else f"{title}  {count}"
        self._mark = mark
        follow_appearance(self, lambda: self.setFixedHeight(Height.ROW.px))

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        """Draw the caption."""
        painter = QPainter(self)
        paint_section_caption(painter, self.rect(), self.palette(), self._text, self._mark(self.palette()))
