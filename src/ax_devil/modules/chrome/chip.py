"""A small outlined tag for a short state such as "Reviewed" or "Failed"."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QSizePolicy, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Space, TextRole, body_px


class ChipKind(Enum):
    """What a chip says: nothing in particular, or a status."""

    NEUTRAL = "neutral"
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"

    def color(self, palette: QPalette) -> QColor:
        """Return the outline and text color on the surface *palette* belongs to."""
        status = {
            ChipKind.OK: StatusColor.SUCCESS,
            ChipKind.WARNING: StatusColor.WARNING,
            ChipKind.ERROR: StatusColor.ERROR,
        }
        if self in status:
            return status[self].color(palette)
        return palette.color(QPalette.ColorRole.PlaceholderText)


class Chip(QWidget):
    """A one-line tag outlined in the color of its *kind*; it takes the room its text needs."""

    def __init__(self, text: str, kind: ChipKind = ChipKind.NEUTRAL, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._kind = kind
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        follow_appearance(self, self._apply_appearance)

    def _apply_appearance(self) -> None:
        width = QFontMetrics(TextRole.SMALL.font()).horizontalAdvance(self._text)
        self.setFixedSize(width + 2 * Space.M, body_px() + Space.XS)
        self.update()

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        """Draw the outline and the text."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._kind.color(self.palette()))
        painter.setFont(TextRole.SMALL.font())
        outline = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(outline, outline.height() / 2, outline.height() / 2)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._text)
