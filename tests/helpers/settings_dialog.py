"""Drive the Settings dialog through the controls and buttons the user sees."""

import pytest
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QLabel, QLineEdit, QMessageBox

from ax_devil.modules.application_shell.configuration_editor import ConfigurationEditor
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from tests.helpers.forms import form_field
from tests.helpers.widgets import button


def choice(dialog: SettingsDialog, label: str) -> QComboBox:
    """Return the drop-down in the row labeled *label*."""
    return form_field(dialog, label, QComboBox)


def choose(combo: QComboBox, value: object) -> None:
    """Select the item of *combo* that stores *value*."""
    index = combo.findData(value)
    assert index >= 0, f"no item stores {value!r}"
    combo.setCurrentIndex(index)


def checkbox(dialog: SettingsDialog, text: str) -> QCheckBox:
    """Return the check box labeled *text*."""
    return next(box for box in dialog.findChildren(QCheckBox) if box.text() == text)


def config_editor(dialog: SettingsDialog, path: str) -> ConfigurationEditor:
    """Return the editor of the configuration value at *path*."""
    return next(editor for editor in dialog.findChildren(ConfigurationEditor) if editor.field.path == path)


def preference_input(dialog: SettingsDialog, path: str) -> QLineEdit:
    """Return the text box the user types the configuration value at *path* into."""
    edit = config_editor(dialog, path).findChild(QLineEdit)
    assert edit is not None
    return edit


def click_ok(dialog: SettingsDialog) -> bool:
    """Click OK and return whether the dialog saved and closed."""
    dialog.setResult(QDialog.DialogCode.Rejected)
    button(dialog, "OK").click()
    return dialog.result() == QDialog.DialogCode.Accepted


def save_error(dialog: SettingsDialog) -> str:
    """Return the error the dialog shows after a failed save, or an empty string."""
    return next(
        (label.text() for label in dialog.findChildren(QLabel) if label.text().startswith("Could not save settings")),
        "",
    )


def answer_restart_prompt(monkeypatch: pytest.MonkeyPatch, answer: str) -> list[str]:
    """Answer every restart question with the button labeled *answer*; return the questions asked."""
    asked: list[str] = []

    def exec_(box: QMessageBox) -> int:
        asked.append(box.text())
        next(choice for choice in box.buttons() if choice.text() == answer).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return asked
