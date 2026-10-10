"""Form controls for a supported scalar configuration preference."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QWidget

from ax_devil.modules.application_shell.configuration_preferences import ConfigField
from ax_devil.modules.chrome.browse_button import BrowseButton
from ax_devil.modules.settings.config_manager import is_environment_reference
from ax_devil.modules.workspace.ui.plugin_intake import default_workspace_intake


class ConfigurationEditor(QWidget):
    """Edit a raw value without silently resolving an environment reference."""

    def __init__(self, field: ConfigField, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.field = field
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._choices: QComboBox | None = None
        self._text: QLineEdit | None = None
        if field.choices or field.decoder:
            combo = QComboBox(self)
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            choices = field.choices
            if field.decoder:
                choices = tuple(
                    (option.display_name, option.handler_type)
                    for option in default_workspace_intake().live_overlay_decoder_options()
                )
            for label, stored in choices:
                combo.addItem(label, stored)
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.setCurrentIndex(-1)
                combo.setEditText(value)
            self._choices = combo
            self.setFocusProxy(combo)
            layout.addWidget(combo, 1)
        else:
            edit = QLineEdit(value, self)
            self._text = edit
            if field.secret:
                edit.textChanged.connect(self._update_secret_display)
                self._update_secret_display(value)
            self.setFocusProxy(edit)
            layout.addWidget(edit, 1)
        if field.directory:
            layout.addWidget(BrowseButton("a folder", self._browse, self))
        self.setToolTip(field.path)

    def text(self) -> str:
        """Return the raw preference represented by the editor."""
        if self._choices is not None:
            combo = self._choices
            index = combo.currentIndex()
            if index >= 0 and combo.currentText() == combo.itemText(index):
                return str(combo.itemData(index))
            return str(combo.currentText())
        assert self._text is not None
        return str(self._text.text())

    def _update_secret_display(self, value: str) -> None:
        assert self._text is not None
        mode = QLineEdit.EchoMode.Normal if is_environment_reference(value) else QLineEdit.EchoMode.Password
        self._text.setEchoMode(mode)

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, self.field.label)
        if selected and self._text is not None:
            self._text.setText(selected)
