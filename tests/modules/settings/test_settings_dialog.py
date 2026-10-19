"""Tests that the Settings dialog shows, validates and saves preferences."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit, QPushButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.theme_mode import ThemeMode
from tests.helpers.forms import form_field
from tests.helpers.settings_dialog import (
    answer_restart_prompt,
    checkbox,
    choice,
    choose,
    click_ok,
    config_editor,
    preference_input,
    save_error,
)
from tests.helpers.widgets import button


def test_theme_dialog_cancel_ok_and_config_round_trip(qtbot: QtBot, config: ConfigManager) -> None:
    """Appearance changes commit on OK, Cancel discards them, and unrelated raw UI settings are kept."""
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    config.set("ui", {"theme": "dark", "window": {"custom_frame": False}, "custom": "$KEEP_RAW"})
    settings = GlobalSettings()
    settings.load_from_config(config)
    changes: list[str] = []
    settings.theme_changed.connect(changes.append)
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    assert choice(dialog, "Theme").currentData() == "dark"
    choose(choice(dialog, "Theme"), "light")
    dialog.reject()
    assert settings.snapshot().theme == ThemeMode.DARK
    assert changes == []

    saved_dialog = SettingsDialog()
    qtbot.addWidget(saved_dialog)
    choose(choice(saved_dialog, "Theme"), "light")
    assert click_ok(saved_dialog)
    assert changes == ["light"]
    saved = json.loads(config.config_path.read_text(encoding="utf-8"))["ui"]
    assert (saved["theme"], saved["window"], saved["custom"]) == ("light", {"custom_frame": False}, "$KEEP_RAW")

    settings.load_from_config(config)
    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    assert choice(reopened, "Theme").currentData() == "light"


def test_all_preferences_persist_and_storage_waits_for_restart(
    qtbot: QtBot, config: ConfigManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    settings = GlobalSettings()
    settings.load_from_config(config)
    config.activate_storage()
    active_storage = config.get("storage")
    answer_restart_prompt(monkeypatch, "Later")
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    edits = {
        "defaults.device.host": "camera.example",
        "defaults.device.password": "$CAMERA_SECRET",
        "defaults.live_stream.analytics-mqtt.broker_port": "8883",
        "defaults.live_stream.analytics-websocket.device_api_protocol": "http",
        "defaults.live_stream.rtsp.data_stream_handler": "UNAVAILABLE_PLUGIN",
        "storage.cache_dir": str(tmp_path / "new-cache"),
    }
    for path, value in edits.items():
        preference_input(dialog, path).setText(value)
    checkbox(dialog, "Use the app title bar (requires restart)").setChecked(False)
    choose(choice(dialog, "Text size"), "large")
    assert click_ok(dialog), save_error(dialog)
    saved = json.loads(config.config_path.read_text())
    assert saved["defaults"]["device"]["password"] == "$CAMERA_SECRET"
    assert saved["defaults"]["device"]["host"] == "camera.example"
    assert saved["defaults"]["live_stream"]["analytics-mqtt"]["broker_port"] == 8883
    assert saved["defaults"]["live_stream"]["analytics-websocket"]["device_api_protocol"] == "http"
    assert saved["defaults"]["live_stream"]["rtsp"]["data_stream_handler"] == "UNAVAILABLE_PLUGIN"
    assert saved["ui"]["window"]["custom_frame"] is False
    assert saved["ui"]["text_size"] == "large"
    assert saved["storage"]["cache_dir"] == str(tmp_path / "new-cache")
    assert config.get("storage") == active_storage
    config.activate_storage()
    assert config.get("storage")["cache_dir"] == str(tmp_path / "new-cache")
    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    assert choice(reopened, "Text size").currentData() == "large"
    assert {path: config_editor(reopened, path).text() for path in edits} == edits


def test_scan_preferences_default_on_and_can_be_cancelled_or_saved(qtbot: QtBot, config: ConfigManager) -> None:
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    settings = GlobalSettings()
    settings.load_from_config(config)
    labels = ("Scan for cameras on startup", "Try to sign in to found cameras by default")
    cancelled = SettingsDialog()
    qtbot.addWidget(cancelled)
    for label in labels:
        assert checkbox(cancelled, label).isChecked()
        checkbox(cancelled, label).setChecked(False)
    cancelled.reject()
    assert settings.scan_on_startup and settings.scan_sign_in

    saved = SettingsDialog()
    qtbot.addWidget(saved)
    for label in labels:
        assert checkbox(saved, label).isChecked()
        checkbox(saved, label).setChecked(False)
    assert click_ok(saved), save_error(saved)
    assert not settings.scan_on_startup and not settings.scan_sign_in
    settings.load_from_config(config)
    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    assert all(not checkbox(reopened, label).isChecked() for label in labels)
    assert json.loads(config.config_path.read_text())["settings"]["network_scan"] == {
        "on_startup": False,
        "sign_in": False,
    }


def test_scan_logins_are_added_removed_and_saved_in_order(qtbot: QtBot, config: ConfigManager) -> None:
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
    from ax_devil.modules.device_discovery.network_scan import Login
    from ax_devil.modules.workspace.ui.logins_editor import LoginsEditor

    GlobalSettings().load_from_config(config)
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    [editor] = dialog.findChildren(LoginsEditor)

    def fields() -> list[QLineEdit]:
        return [edit for edit in editor.findChildren(QLineEdit) if edit.isVisibleTo(editor)]

    assert [edit.text() for edit in fields()] == ["$AX_DEVIL_TARGET_USER", "$AX_DEVIL_TARGET_PASS"], "the default login"
    for username, password in (("operator", "$OPERATOR_PASS"), ("root", "pass"), ("", "ignored")):
        button(editor, "Add login").click()
        *_, user_field, password_field = fields()
        user_field.setText(username)
        password_field.setText(password)
    [remove_first, *_] = [found for found in editor.findChildren(QPushButton) if found.toolTip() == "Remove this login"]
    remove_first.click()
    assert click_ok(dialog), save_error(dialog)

    assert Login.saved(config) == (Login("operator", "$OPERATOR_PASS"), Login("root", "pass"))
    assert json.loads(config.config_path.read_text())["defaults"]["network_scan"]["credentials"] == [
        {"username": "operator", "password": "$OPERATOR_PASS"},
        {"username": "root", "password": "pass"},
    ]


def test_invalid_transport_and_failed_save_do_not_partially_apply(
    qtbot: QtBot, config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    settings = GlobalSettings()
    settings.load_from_config(config)
    config.save()
    previous_document = config.config_path.read_bytes()
    previous_files = set(config.config_path.parent.iterdir())
    previous_snapshot = settings.snapshot()
    changes: list[tuple[str, Any]] = []
    settings.setting_changed.connect(lambda key, value: changes.append((key, value)))
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    choose(choice(dialog, "Theme"), "light")
    checkbox(dialog, OverlayPreference.HOVER.label).setChecked(False)
    choose(choice(dialog, "Memory for video caching"), False)
    form_field(dialog, "Total allowance", QDoubleSpinBox).setValue(0.25)
    port = preference_input(dialog, "defaults.live_stream.analytics-mqtt.broker_port")
    port.setText("99999")
    assert not click_ok(dialog)
    assert "Broker port" in save_error(dialog)
    assert settings.snapshot() == previous_snapshot
    assert config.config_path.read_bytes() == previous_document
    assert changes == []
    port.setText("8883")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("Read-only configuration")

    with monkeypatch.context() as patcher:
        patcher.setattr("ax_devil.core.atomic_file.replace_file", fail_replace)
        assert not click_ok(dialog)
    assert settings.snapshot() == previous_snapshot
    assert config.config_path.read_bytes() == previous_document
    assert config.get_raw("defaults")["live_stream"]["analytics-mqtt"]["broker_port"] == 1883
    assert set(config.config_path.parent.iterdir()) == previous_files
    assert changes == []
    assert click_ok(dialog), save_error(dialog)
    assert settings.theme == ThemeMode.LIGHT


@pytest.mark.parametrize(
    "path,reference",
    [
        ("defaults.device.password", "$AX_DEVIL_TARGET_PASS"),
        ("defaults.live_stream.analytics-mqtt.broker_password", "$AX_DEVIL_MQTT_BROKER_PASS"),
    ],
)
def test_password_references_are_visible_and_literal_passwords_masked(
    qtbot: QtBot, config: ConfigManager, monkeypatch: pytest.MonkeyPatch, path: str, reference: str
) -> None:
    from PySide6.QtWidgets import QLineEdit

    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    monkeypatch.setenv(reference[1:], "synthetic-resolved-secret")
    settings = GlobalSettings()
    settings.load_from_config(config)
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    edit = preference_input(dialog, path)
    assert edit.echoMode() == QLineEdit.EchoMode.Normal
    assert edit.displayText() == reference

    edit.setText("synthetic-literal-secret")
    assert edit.echoMode() == QLineEdit.EchoMode.Password
    assert edit.displayText() != edit.text()

    edit.setText("~$not a reference")
    assert edit.echoMode() == QLineEdit.EchoMode.Password
    assert click_ok(dialog), save_error(dialog)
    root, *keys = path.split(".")
    saved: Any = config.get(root)
    for key in keys:
        saved = saved[key]
    assert saved == "~$not a reference"
    edit.setText("$REPLACEMENT_SECRET")
    assert edit.echoMode() == QLineEdit.EchoMode.Normal
    assert edit.displayText() == "$REPLACEMENT_SECRET"
    assert click_ok(dialog), save_error(dialog)
    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    assert preference_input(reopened, path).displayText() == "$REPLACEMENT_SECRET"
