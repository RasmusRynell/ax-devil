"""Small dialog asking what plugin to install: a package requirement, a wheel or a plugin source folder."""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QMenu, QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.browse_button import BrowseButton
from ax_devil.modules.chrome.tokens import Space


class AddPluginDialog(BaseDialog):
    """One field for the plugin to install, with a button that picks a folder or a wheel for it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Add plugin", scroll_content=False)
        self.setMinimumHeight(0)
        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText("Package, wheel or folder")
        self._source_edit.setMinimumWidth(self._source_edit.fontMetrics().averageCharWidth() * 40)
        self._browse_menu = QMenu(self)
        self._browse_menu.addAction("Folder", self._choose_folder)
        self._browse_menu.addAction("Wheel", self._choose_wheel)
        self._browse_button = BrowseButton("a folder or wheel", self._browse)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)
        layout.addWidget(self._source_edit, 1)
        layout.addWidget(self._browse_button)
        self.add_content_widget(row)
        install, _cancel = self.add_standard_buttons()
        install.setText("Install")
        install.setEnabled(False)
        self._source_edit.textChanged.connect(lambda: install.setEnabled(bool(self.source())))

    def source(self) -> str:
        """Return what to install, without surrounding whitespace."""
        return self._source_edit.text().strip()

    def _browse(self) -> None:
        self._browse_menu.popup(self._browse_button.mapToGlobal(self._browse_button.rect().bottomLeft()))

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Plugin folder")
        if folder:
            self._source_edit.setText(folder)

    def _choose_wheel(self) -> None:
        wheel, _ = QFileDialog.getOpenFileName(self, "Plugin wheel", "", "Wheels (*.whl)")
        if wheel:
            self._source_edit.setText(wheel)
