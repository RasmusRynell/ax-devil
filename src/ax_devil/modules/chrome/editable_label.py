"""Elided label that becomes a text field when clicked."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import QLineEdit, QWidget

from ax_devil.modules.chrome.elided_label import ElidedLabel


class _EscapableLineEdit(QLineEdit):
    """A text field that says so when Escape is pressed."""

    escaped = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Report Escape instead of handling it."""
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
        else:
            super().keyPressEvent(event)


class EditableLabel(ElidedLabel):
    """An elided label whose text is edited in place once it is clicked.

    Enter or leaving the field ends the edit and emits ``edited`` with the entered text when it changed; Escape ends
    it without. The label shows the new text only when its owner sets it, so a refused edit leaves the old one.
    """

    edited = Signal(str)

    def __init__(self, text: str, elide: Qt.TextElideMode, parent: QWidget | None = None) -> None:
        super().__init__(text, elide, parent)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self._field = _EscapableLineEdit(self)
        self._field.hide()
        self._field.editingFinished.connect(self._finish)
        self._field.escaped.connect(self._field.hide)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Start editing on a left click."""
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._field.setText(self.full_text())
        self._field.setGeometry(self.rect())
        self._field.show()
        self._field.setFocus()
        self._field.selectAll()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Keep the field over the label."""
        super().resizeEvent(event)
        self._field.setGeometry(self.rect())

    def _finish(self) -> None:
        # Hiding the field also takes its focus and finishes it again, by when it is hidden and this returns.
        if not self._field.isVisible():
            return
        text = self._field.text()
        self._field.hide()
        if text.strip() != self.full_text():
            self.edited.emit(text)
