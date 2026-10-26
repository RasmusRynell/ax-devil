"""Small dialog asking for a new name, such as a bookmark's."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLineEdit, QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog


class RenameDialog(BaseDialog):
    """Edit one name; an empty name returns it to its default name."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Rename", scroll_content=False)
        self.setMinimumHeight(0)
        self._name_edit = QLineEdit(name)
        self._name_edit.selectAll()
        self.add_content_widget(self._name_edit)
        self.add_standard_buttons()

    def name(self) -> str:
        """Return the entered name without surrounding whitespace."""
        return self._name_edit.text().strip()


def ask_new_name(parent: QWidget | None, current: str, default_name: str) -> str | None:
    """Ask for a new name for something now called *current*, whose name without a label of its own is *default_name*.

    Return None when the user cancels, and an empty string when the name entered is empty or *default_name*, which
    returns the thing to its default name; otherwise return the name entered.
    """
    with RenameDialog(current, parent) as dialog:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name = dialog.name()
    return "" if name == default_name else name
