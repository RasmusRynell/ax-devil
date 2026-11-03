"""Keyboard keys drawn as small chips, such as [Ctrl] [N], as the welcome screen and the shortcut editor show them."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from PySide6.QtCore import QKeyCombination, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
)
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.tokens import Radius, Space, TextRole

_H_PAD = Space.S
_V_PAD = Space.XS
_GAP = Space.S


CHORD_BREAK = ""
"""Placed between the chips of two chords, such as Ctrl+K then Ctrl+S; drawn as a wider gap."""


def key_chip_parts(sequence: QKeySequence) -> list[str]:
    """Return the keys of *sequence* in the platform's own names, one per chip, with ``CHORD_BREAK`` between chords."""
    parts: list[str] = []
    native = QKeySequence.SequenceFormat.NativeText
    for index in range(sequence.count()):
        combination: QKeyCombination = sequence[index]  # type: ignore[index]  # PySide stubs lack __getitem__.
        key = QKeySequence(QKeyCombination(combination.key())).toString(native)
        modifiers = QKeySequence(combination).toString(native).removesuffix(key)
        # Modifiers read "Ctrl+Shift+" on most platforms and "⇧⌘" on macOS.
        names = [name for name in modifiers.split("+") if name] if "+" in modifiers else list(modifiers)
        if parts:
            parts.append(CHORD_BREAK)
        parts.extend([*names, key])
    return parts


def _chip_width(metrics: QFontMetricsF, part: str) -> float:
    return _GAP if part == CHORD_BREAK else metrics.horizontalAdvance(part) + 2 * _H_PAD


def key_chips_width(metrics: QFontMetricsF, parts: list[str]) -> float:
    """Return the width of the chips for *parts*."""
    return sum(_chip_width(metrics, part) for part in parts) + _GAP * max(0, len(parts) - 1)


def key_chips_height(metrics: QFontMetricsF) -> float:
    """Return the height of one row of chips."""
    return metrics.height() + 2 * _V_PAD


@dataclass(frozen=True, slots=True)
class KeyChipColors:
    """Text, fill and border colors of key chips on one surface."""

    text: QColor
    background: QColor
    border: QColor

    @classmethod
    def for_palette(cls, palette: QPalette) -> KeyChipColors:
        """Return chip colors that stand out a little from *palette*'s window color."""
        text = QColor(palette.color(QPalette.ColorRole.Text))
        text.setAlphaF(0.85)
        window = palette.color(QPalette.ColorRole.Window)
        dark = window.lightnessF() < 0.5
        background = window.lighter(160) if dark else window.darker(110)
        border = background.lighter(130) if dark else background.darker(120)
        return cls(text=text, background=background, border=border)


def draw_key_chips(
    painter: QPainter,
    font: QFont,
    parts: list[str],
    x: float,
    y: float,
    colors: KeyChipColors,
) -> None:
    """Draw the chips for *parts* with their top-left corner at (*x*, *y*)."""
    metrics = QFontMetricsF(font)
    painter.setFont(font)
    height = key_chips_height(metrics)
    for part in parts:
        width = _chip_width(metrics, part)
        if part == CHORD_BREAK:
            x += width + _GAP
            continue
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, width, height), Radius.CONTROL, Radius.CONTROL)
        painter.fillPath(path, colors.background)
        painter.setPen(colors.border)
        painter.drawPath(path)
        painter.setPen(colors.text)
        painter.drawText(QPointF(x + _H_PAD, y + _V_PAD + metrics.ascent()), part)
        x += width + _GAP


_ACTIVATE_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space, Qt.Key.Key_F2)


class KeyChips(QWidget):
    """A shortcut shown as key chips, or muted placeholder text when it has none; emits ``clicked`` when clicked."""

    clicked = Signal()

    def __init__(self, sequence: QKeySequence, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parts: list[str] = []
        self._placeholder = placeholder
        self._preferred_width = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_key_sequence(sequence)

    def set_key_sequence(self, sequence: QKeySequence) -> None:
        """Show *sequence* as chips."""
        self._parts = key_chip_parts(sequence)
        self.updateGeometry()
        self.update()

    def set_preferred_width(self, width: int) -> None:
        """Prefer at least *width*, such as the width of an editor that replaces the chips while editing."""
        self._preferred_width = width
        self.updateGeometry()

    def parts(self) -> list[str]:
        """Return the keys shown, one per chip."""
        return list(self._parts)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return room for the chips, widened to the preferred width."""
        hint = self.minimumSizeHint()
        return QSize(max(hint.width(), self._preferred_width), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return room for the chips, or for the placeholder; chips are never cut."""
        metrics = QFontMetricsF(TextRole.MONO_SMALL.font())
        if self._parts:
            width = key_chips_width(metrics, self._parts)
        else:
            width = QFontMetricsF(self.font()).horizontalAdvance(self._placeholder)
        return QSize(ceil(width) + 2, ceil(key_chips_height(metrics)) + 2)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Emit ``clicked`` for a left click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Emit ``clicked`` for Enter, Space or F2, so the keyboard reaches what a click does."""
        if event.key() in _ACTIVATE_KEYS and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        """Draw the chips right-aligned and vertically centered."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._parts:
            font = TextRole.MONO_SMALL.font()
            metrics = QFontMetricsF(font)
            x = self.width() - 1 - key_chips_width(metrics, self._parts)
            y = (self.height() - key_chips_height(metrics)) / 2
            draw_key_chips(painter, font, self._parts, x, y, KeyChipColors.for_palette(self.palette()))
        else:
            painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(
                self.rect(), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), self._placeholder
            )
        if self.hasFocus():
            painter.setPen(self.palette().color(QPalette.ColorRole.Highlight))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), Radius.CONTROL, Radius.CONTROL)
        painter.end()
