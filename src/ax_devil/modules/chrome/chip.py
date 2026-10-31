"""A small outlined tag for a short state such as "Reviewed" or "Failed", and a row of chips to choose one from."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPalette
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Space, TextRole, body_px

_HOVER_ALPHA = 0.14
_ACTIVATE_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)


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

    A *clickable* chip shows a pointing hand and a fill under the pointer, takes keyboard focus with an accent outline,
    and emits ``clicked`` on a left click, Space or Enter.
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
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.setAccessibleName(text)
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
        """Repaint when the pointer enters or leaves a clickable chip; its keys beat application shortcuts."""
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        elif (
            event.type() == QEvent.Type.ShortcutOverride
            and self._clickable
            and isinstance(event, QKeyEvent)
            and event.key() in _ACTIVATE_KEYS
            and not event.modifiers()
        ):
            event.accept()  # Space would otherwise play or pause the video instead of choosing the chip.
            return True
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

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Emit ``clicked`` for Space or Enter on a clickable chip, so the keyboard reaches what a click does."""
        if self._clickable and event.key() in _ACTIVATE_KEYS:
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        """Draw the outline, a fill under the pointer when clickable, and the text."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._kind.color(self.palette())
        if self._clickable and self.underMouse():
            fill = QColor(color)
            fill.setAlphaF(_HOVER_ALPHA)
            painter.setBrush(fill)
        painter.setPen(self.palette().color(QPalette.ColorRole.Highlight) if self.hasFocus() else color)
        painter.setFont(TextRole.SMALL.font())
        outline = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(outline, outline.height() / 2, outline.height() / 2)
        painter.setPen(color)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._text)


def paint_tag(painter: QPainter, rect: QRect, text: str, color: QColor) -> int:
    """Draw *text* as an outlined tag in *color*, left-aligned and vertically centered in *rect*; return its width.

    For rows that paint themselves. The tag looks like a ``Chip`` and is elided when *rect* is too narrow for it.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = TextRole.SMALL.font()
    metrics = QFontMetrics(font)
    shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, rect.width() - 2 * Space.M))
    width = metrics.horizontalAdvance(shown) + 2 * Space.M
    height = body_px() + Space.XS
    tag = QRect(rect.left(), rect.top() + (rect.height() - height) // 2, width, height)
    painter.setPen(color)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setFont(font)
    outline = QRectF(tag).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.drawRoundedRect(outline, outline.height() / 2, outline.height() / 2)
    painter.drawText(tag, int(Qt.AlignmentFlag.AlignCenter), shown)
    painter.restore()
    return width


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
