"""Segmented control that picks one of a few labeled values."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from PySide6.QtCore import QSize, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QWidget

from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole


class ChoiceToggle(QFrame):
    """Two or three checkable buttons in one outlined frame; exactly one is selected and ``changed`` reports it.

    The buttons take the theme's checked look, so the selected segment is accented inside the frame's outline.
    """

    changed = Signal(str)

    def __init__(self, choices: Sequence[tuple[str, str]], current: str, parent: QWidget | None = None) -> None:
        """Show one button per ``(value, label)`` choice, with *current* selected (the first if it is unknown)."""
        super().__init__(parent)
        self._labels = [label for _value, label in choices]
        self._value = current if current in {value for value, _label in choices} else choices[0][0]
        self.setStyleSheet(f"ChoiceToggle {{ border: 1px solid palette(mid); border-radius: {Radius.CONTROL}px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        group = QButtonGroup(self)
        for value, label in choices:
            button = QPushButton(label, self)
            button.setCheckable(True)
            button.setAutoDefault(False)  # Enter in a dialog confirms the dialog, not this button
            button.setChecked(value == self._value)
            button.toggled.connect(partial(self._select, value))
            group.addButton(button)
            layout.addWidget(button)
            if button.isChecked():
                self.setFocusProxy(button)

    def value(self) -> str:
        """Return the selected value."""
        return self._value

    def size_at(self, text_px: int) -> QSize:
        """Return the size at *text_px* body text, for dialogs that open with room for the largest text size."""
        metrics = QFontMetrics(TextRole.BODY.font_at(text_px))
        padding = 2 * (Space.M + 1)  # the theme's button padding and border
        width = sum(metrics.horizontalAdvance(label) + padding for label in self._labels)
        return QSize(width + 2, text_px + Height.CONTROL.value + 2)

    def _select(self, value: str, checked: bool) -> None:
        if checked:
            self._value = value
            self.changed.emit(value)
