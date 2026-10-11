"""A small outlined tag for a short state such as "Reviewed" or "Failed", and a row of chips to choose one from."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPalette
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Space, TextRole, body_px

_HOVER_ALPHA = 0.14


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
    """A one-line tag outlined in the color of its *kind*; it takes the room its text needs.

    A *clickable* chip shows a pointing hand and a fill under the pointer, and emits ``clicked`` on a left click.
    """

    clicked = Signal()

    def __init__(
        self, text: str, kind: ChipKind = ChipKind.NEUTRAL, *, clickable: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._kind = kind
        self._clickable = clickable
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        follow_appearance(self, self._apply_appearance)

    def text(self) -> str:
        """Return the chip's text."""
        return self._text

    def kind(self) -> ChipKind:
        """Return what the chip says."""
        return self._kind

    def set_kind(self, kind: ChipKind) -> None:
        """Outline the chip in the color of *kind*."""
        self._kind = kind
        self.update()

    def _apply_appearance(self) -> None:
        width = QFontMetrics(TextRole.SMALL.font()).horizontalAdvance(self._text)
        self.setFixedSize(width + 2 * Space.M, body_px() + Space.XS)
        self.update()

    def event(self, event: QEvent) -> bool:
        """Repaint when the pointer enters or leaves a clickable chip."""
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Emit ``clicked`` for a left click that ends on a clickable chip."""
        if (
            self._clickable
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        """Draw the outline, a fill under the pointer when clickable, and the text."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._kind.color(self.palette())
        if self._clickable and self.underMouse():
            fill = QColor(color)
            fill.setAlphaF(_HOVER_ALPHA)
            painter.setBrush(fill)
        painter.setPen(color)
        painter.setFont(TextRole.SMALL.font())
        outline = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(outline, outline.height() / 2, outline.height() / 2)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._text)


class ChoiceChips(QWidget):
    """A row of clickable chips of which exactly one is chosen, outlined as ``ChipKind.OK``; the first to start with."""

    choice_changed = Signal(int)  # The index of the chip now chosen.

    def __init__(self, texts: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)
        self._chips = [Chip(text, clickable=True, parent=self) for text in texts]
        for index, chip in enumerate(self._chips):
            chip.clicked.connect(lambda index=index: self.choose(index))
            layout.addWidget(chip)
        layout.addStretch(1)
        self._chosen = 0
        self._show_chosen()

    def chips(self) -> tuple[Chip, ...]:
        """Return the chips, in order."""
        return tuple(self._chips)

    def chosen(self) -> int:
        """Return the index of the chosen chip."""
        return self._chosen

    def choose(self, index: int) -> None:
        """Choose the chip at *index*, and emit ``choice_changed`` when that changes the choice."""
        if index == self._chosen:
            return
        self._chosen = index
        self._show_chosen()
        self.choice_changed.emit(index)

    def _show_chosen(self) -> None:
        for index, chip in enumerate(self._chips):
            chip.set_kind(ChipKind.OK if index == self._chosen else ChipKind.NEUTRAL)
