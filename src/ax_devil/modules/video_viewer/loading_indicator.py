"""Reusable loading overlay widgets."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPaintEvent, QPalette, QPen, QResizeEvent
from PySide6.QtWidgets import QWidget


class LoadingIndicator(QWidget):
    """Animated spinner with status text, shown while content loads."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._message = ""
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if parent is not None:
            parent.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: object) -> bool:  # noqa: N802
        """Track parent resizes so the indicator always covers the full area."""
        if isinstance(event, QResizeEvent) and obj is self.parent() and isinstance(obj, QWidget):
            self.setGeometry(obj.rect())
        return False

    def set_message(self, message: str) -> None:
        """Update the displayed status message."""
        self._message = message
        self.update()

    def start(self) -> None:
        """Start the spinner animation."""
        self._timer.start(25)

    def stop(self) -> None:
        """Stop the spinner animation."""
        self._timer.stop()

    def _tick(self) -> None:
        self._angle = (self._angle - 8) % 360
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        """Paint the spinning arc and status message."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = self.palette().color(QPalette.ColorRole.Window)
        bg.setAlpha(200)
        painter.fillRect(self.rect(), bg)

        color = self.palette().color(QPalette.ColorRole.PlaceholderText)
        cx = self.width() / 2
        cy = self.height() / 2

        arc_size = 28.0
        arc_rect = QRectF(cx - arc_size / 2, cy - arc_size / 2 - 14, arc_size, arc_size)
        pen = QPen(color, 3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(arc_rect, int(self._angle * 16), int(270 * 16))

        if self._message:
            painter.setPen(color)
            painter.setFont(self.font())
            text_rect = QRectF(0, cy + arc_size / 2, self.width(), 30)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self._message,
            )

        painter.end()
