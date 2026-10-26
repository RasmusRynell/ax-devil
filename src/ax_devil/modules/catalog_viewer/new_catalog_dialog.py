"""The dialog that names a new catalog made as a copy of another."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.chrome.form_layout import FormLayout


class NewCatalogDialog(BaseDialog):
    """Ask for the name of a new catalog that starts as a copy of another."""

    def __init__(self, source: str, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent, title="New Catalog")
        content = QWidget(self)
        form = FormLayout(content)
        self.name_edit = QLineEdit(f"{source} copy", content)
        self.name_edit.setObjectName("newCatalogName")
        self.name_edit.selectAll()
        form.addRow("Name", self.name_edit)
        note = QLabel(f"Starts as a copy of {source}.", content)
        note.setWordWrap(True)
        form.addRow("", note)
        self.add_content_widget(content)
        ok, _cancel = self.add_standard_buttons()
        ok.setText("Create")
        self.name_edit.textChanged.connect(lambda text: ok.setEnabled(bool(text.strip())))

    def name(self) -> str:
        """Return the entered name."""
        return self.name_edit.text().strip()
