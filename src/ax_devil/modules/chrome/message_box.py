"""Question boxes whose buttons always show their full text."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from ax_devil.modules.chrome.tokens import Space

Button = QMessageBox.StandardButton
_BUTTON_GAP = 2 * Space.M


class ChromeMessageBox(QMessageBox):
    """A message box wide enough for its button row.

    Qt fixes a message box's width from its text alone and ignores the theme's button padding, so a row of
    buttons is squeezed and their text cut off. Just before showing, a label minimum width equal to the buttons'
    preferred widths widens the box. Use this instead of `QMessageBox` for any box with a question to answer.
    """

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt override
        """Widen the box to its buttons before it is shown."""
        if visible:
            buttons = self.buttons()
            row = sum(button.sizeHint().width() for button in buttons) + _BUTTON_GAP * len(buttons)
            self.setStyleSheet(f"QLabel {{ min-width: {row}px; }}")
        super().setVisible(visible)


def ask(parent: QWidget | None, title: str, text: str, buttons: Button, default: Button) -> Button:
    """Show a question with standard *buttons* and return the one the user chose (Escape gives Cancel or No)."""
    box = ChromeMessageBox(QMessageBox.Icon.Question, title, text, buttons, parent)
    box.setDefaultButton(default)
    box.exec()
    return box.standardButton(box.clickedButton())
