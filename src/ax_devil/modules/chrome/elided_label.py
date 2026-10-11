"""One-line label that elides its text instead of widening its container."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QLabel):
    """A one-line label that takes the width it is given and elides its text to fit; the tooltip has it whole."""

    def __init__(self, text: str, elide: Qt.TextElideMode, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._elide = elide
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def full_text(self) -> str:
        """Return the text before eliding."""
        return self._full_text

    def set_full_text(self, text: str) -> None:
        """Show *text*, elided to fit."""
        self._full_text = text
        self.setToolTip(text)
        self._update_text()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Elide again at the new width."""
        super().resizeEvent(event)
        self._update_text()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """Elide again when the font changes."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._update_text()

    def _update_text(self) -> None:
        text = self.fontMetrics().elidedText(self._full_text, self._elide, max(0, self.width()))
        if text != self.text():
            self.setText(text)
