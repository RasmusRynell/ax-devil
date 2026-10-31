"""The title row of an object: class-colored dot, class and a state pill."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chip import paint_tag
from ax_devil.modules.chrome.theme import category_color
from ax_devil.modules.chrome.tokens import Height, Space, TextRole

_DIMMED_OPACITY = 0.4


class TitleRow(QWidget):
    """The object's class-colored dot, its class in bold and a pill for its state."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._class_name = ""
        self._pill = ""
        self._pill_category: str | None = None
        self._dimmed = False
        self.setStyleSheet("background: transparent;")
        follow_appearance(self, lambda: self.setFixedHeight(Height.PANE_HEADER.px))

    def set_content(self, class_name: str, pill: str, pill_category: str | None, *, dimmed: bool) -> None:
        """Show the *class_name* and the *pill*; a pill with a *pill_category* is colored by it, else muted."""
        content = (class_name, pill, pill_category, dimmed)
        if content != (self._class_name, self._pill, self._pill_category, self._dimmed):
            self._class_name, self._pill, self._pill_category, self._dimmed = content
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the dot, class and pill from left to right."""
        painter = QPainter(self)
        palette = self.palette()
        rect = self.rect().adjusted(Space.M, 0, -Space.M, 0)
        dot = max(6, TextRole.BODY.px * 2 // 3)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(category_color(self._class_name, palette))
        painter.setOpacity(_DIMMED_OPACITY if self._dimmed else 1.0)
        painter.drawEllipse(QRect(rect.left(), rect.top() + (rect.height() - dot) // 2, dot, dot))
        painter.setOpacity(1.0)
        left = rect.left() + dot + Space.M
        pill_px = QFontMetrics(TextRole.SMALL.font()).horizontalAdvance(self._pill) + 2 * Space.M if self._pill else 0
        # The state is short and says more than the class does, so the class gives way to it.
        class_room = rect.right() + 1 - left - (pill_px + Space.M if pill_px else 0)
        class_px = self._draw(painter, left, rect, TextRole.STRONG.font(), self._class_name, class_room)
        left += class_px + Space.M
        cut = (
            class_px < QFontMetrics(TextRole.STRONG.font()).horizontalAdvance(self._class_name)
            or left + pill_px > rect.right() + 1
        )
        tip = f"{self._class_name} {self._pill}".strip() if cut else ""
        if tip != self.toolTip():
            self.setToolTip(tip)
        if self._pill and left < rect.right():
            color = (
                category_color(self._pill_category, palette)
                if self._pill_category is not None
                else palette.color(QPalette.ColorRole.PlaceholderText)
            )
            paint_tag(painter, QRect(left, rect.top(), rect.right() - left + 1, rect.height()), self._pill, color)

    def _draw(self, painter: QPainter, left: int, rect: QRect, font: QFont, text: str, max_width: int) -> int:
        """Draw *text* at *left* in at most *max_width*, and return the width it took."""
        painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        metrics = QFontMetrics(font)
        shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, max_width))
        width = metrics.horizontalAdvance(shown)
        painter.drawText(
            QRect(left, rect.top(), width + 1, rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            shown,
        )
        return width
