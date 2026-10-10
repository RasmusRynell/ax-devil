"""Small dialog asking for a Workspace Item's new name."""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog


class RenameDialog(BaseDialog):
    """Edit one name; an empty name returns the item to its default name."""

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
