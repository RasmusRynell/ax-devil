"""Tests for GlobalSettings."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.playback_settings import VideoCacheBudget
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.settings.theme_mode import ThemeMode


@pytest.fixture(autouse=True)
def _reset_singleton() -> Iterator[None]:
    """Ensure each test gets a fresh GlobalSettings instance."""
    GlobalSettings.reset_instance()
    yield
    GlobalSettings.reset_instance()


class TestGlobalSettingsSignals:
    """Verify signal emission on property changes."""

    def test_overlay_preference_defaults_on_and_emits_signals_on_change_only(self) -> None:
        settings = GlobalSettings()
        preference_changes: list[tuple[OverlayPreference, bool]] = []
        setting_changes: list[tuple[str, Any]] = []
        settings.overlay_preference_changed.connect(lambda key, val: preference_changes.append((key, val)))
        settings.setting_changed.connect(lambda key, val: setting_changes.append((key, val)))

        assert all(settings.is_overlay_enabled(preference) for preference in OverlayPreference)
        settings.set_overlay_enabled(OverlayPreference.HOVER, True)
        assert preference_changes == []
        assert setting_changes == []

        settings.set_overlay_enabled(OverlayPreference.HOVER, False)

        assert preference_changes == [(OverlayPreference.HOVER, False)]
        assert setting_changes == [("overlay_interaction.hover_enabled", False)]


class TestGlobalSettingsConfigRoundTrip:
    """Verify load/save with ConfigManager."""

    @pytest.mark.parametrize("saved,expected", [({}, True), ({"overlay_interaction": {"hover_enabled": False}}, False)])
    def test_load_from_config_uses_config_or_default(
        self, config: ConfigManager, saved: dict[str, Any], expected: bool
    ) -> None:
        config.set("settings", saved)
        settings = GlobalSettings()
        settings.load_from_config(config)
        assert settings.is_overlay_enabled(OverlayPreference.HOVER) is expected

    def test_save_to_config_preserves_raw_settings(
        self, config: ConfigManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AX_DEVIL_TEST_SETTING", "resolved-value")
        config.set("settings", {"custom": {"keep": "$AX_DEVIL_TEST_SETTING"}})

        settings = GlobalSettings()
        settings.theme = ThemeMode.DARK
        settings.graphics_acceleration = GraphicsAcceleration.OFF
        settings.text_size = TextSize.LARGER
        settings.custom_frame = False
        settings.quick_setup_done = True
        settings.video_cache_budget = VideoCacheBudget(512)
        for preference in OverlayPreference:
            settings.set_overlay_enabled(preference, False)
        expected = settings.snapshot()
        settings.save_to_config(config)
        config.save()

        saved = json.loads(config.config_path.read_text(encoding="utf-8"))
        assert saved["settings"]["custom"] == {"keep": "$AX_DEVIL_TEST_SETTING"}
        monkeypatch.setattr(ConfigManager, "_instance", None)
        reopened = ConfigManager()
        reopened.set_config_path(config.config_path, create_if_missing=False)
        GlobalSettings.reset_instance()
        restored = GlobalSettings()
        restored.load_from_config(reopened)
        assert restored.snapshot() == expected
        assert not any(restored.is_overlay_enabled(preference) for preference in OverlayPreference)
        assert reopened.get("settings")["custom"] == {"keep": "resolved-value"}


class TestGlobalSettingsSnapshot:
    """Verify snapshot / apply_snapshot round-trip."""

    def test_snapshot_round_trip_emits_on_change_only(self) -> None:
        settings = GlobalSettings()
        received: list[bool] = []
        settings.overlay_preference_changed.connect(lambda _preference, enabled: received.append(enabled))

        settings.set_overlay_enabled(OverlayPreference.HOVER, False)

        snap = settings.snapshot()

        settings.apply_snapshot(replace(settings.snapshot(), enabled_overlays=frozenset(OverlayPreference)))
        assert settings.is_overlay_enabled(OverlayPreference.HOVER) is True
        assert received == [False, True]

        settings.apply_snapshot(snap)
        assert settings.is_overlay_enabled(OverlayPreference.HOVER) is False
        assert received == [False, True, False]
        settings.apply_snapshot(snap)
        assert received == [False, True, False]


def test_graphics_acceleration_round_trip_does_not_apply_until_startup(
    config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("QT_WIDGETS_RHI", raising=False)
    settings = GlobalSettings()
    changes: list[tuple[str, Any]] = []
    settings.setting_changed.connect(lambda key, value: changes.append((key, value)))
    settings.apply_snapshot(replace(settings.snapshot(), graphics_acceleration=GraphicsAcceleration.OFF))
    settings.apply_snapshot(replace(settings.snapshot(), graphics_acceleration=GraphicsAcceleration.OFF))
    assert changes == [("appearance.graphics_acceleration", "off")]
    assert settings.graphics_acceleration is GraphicsAcceleration.OFF
    assert "QT_WIDGETS_RHI" not in os.environ
    settings.save_to_config(config)
    GlobalSettings.reset_instance()
    restored = GlobalSettings()
    restored.load_from_config(config)
    assert restored.graphics_acceleration is GraphicsAcceleration.OFF


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
    assert dialog._theme_combo.currentData() == "dark"
    dialog._theme_combo.setCurrentIndex(dialog._theme_combo.findData("light"))
    dialog.reject()
    assert settings.snapshot().theme == ThemeMode.DARK
    assert changes == []

    saved_dialog = SettingsDialog()
    qtbot.addWidget(saved_dialog)
    saved_dialog._theme_combo.setCurrentIndex(saved_dialog._theme_combo.findData("light"))
    saved_dialog._on_ok()
    assert changes == ["light"]
    discarded = SettingsDialog()
    qtbot.addWidget(discarded)
    discarded._theme_combo.setCurrentIndex(discarded._theme_combo.findData("dark"))
    discarded.reject()
    assert settings.theme == ThemeMode.LIGHT and changes == ["light"]
    settings.save_to_config(config)
    config.save()
    saved = json.loads(config.config_path.read_text(encoding="utf-8"))
    assert saved["ui"] == {
        "theme": "light",
        "text_size": "system",
        "quick_setup_done": False,
        "window": {"custom_frame": False},
        "custom": "$KEEP_RAW",
    }

    settings.load_from_config(config)
    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    assert reopened._theme_combo.currentData() == "light"
    reopened._theme_combo.setCurrentIndex(reopened._theme_combo.findData("auto"))
    reopened._on_ok()
    assert settings.snapshot().theme == ThemeMode.AUTO
    assert changes == ["light", "auto"]


@pytest.mark.parametrize("invalid", ["unknown", {}])
def test_invalid_theme_follows_system(invalid: object) -> None:
    assert ThemeMode.from_config(invalid) is ThemeMode.AUTO


@pytest.mark.parametrize("invalid", ["huge", {}])
def test_invalid_text_size_follows_system(invalid: object) -> None:
    assert TextSize.from_config(invalid) is TextSize.SYSTEM


def test_all_preferences_persist_and_storage_waits_for_restart(
    qtbot: QtBot, config: ConfigManager, tmp_path: Path
) -> None:
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    settings = GlobalSettings()
    settings.load_from_config(config)
    config.activate_storage()
    active_storage = config.get("storage")
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
    for field, editor in dialog._configuration_editors.items():
        if field.path in edits:
            if editor._text is not None:
                editor._text.setText(edits[field.path])
            else:
                assert editor._choices is not None
                editor._choices.setEditText(edits[field.path])
    dialog._custom_frame.setChecked(False)
    dialog._text_size_combo.setCurrentIndex(dialog._text_size_combo.findData("large"))
    assert dialog._apply()
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
    assert reopened._text_size_combo.currentData() == "large"
    for field, editor in reopened._configuration_editors.items():
        if field.path in edits:
            assert editor.text() == edits[field.path]


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
    dialog._theme_combo.setCurrentIndex(dialog._theme_combo.findData("light"))
    dialog._overlay_checkboxes[OverlayPreference.HOVER].setChecked(False)
    dialog._video_cache_mode.setCurrentIndex(1)
    dialog._video_cache_spin.setValue(0.25)
    port = next(editor for field, editor in dialog._configuration_editors.items() if field.path.endswith("broker_port"))
    assert port._text is not None
    port._text.setText("99999")
    assert not dialog._apply()
    assert "Broker port" in dialog._status_label.text()
    assert settings.snapshot() == previous_snapshot
    assert config.config_path.read_bytes() == previous_document
    assert changes == []
    port._text.setText("8883")

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("Read-only configuration")

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "replace", fail_replace)
        assert not dialog._apply()
    assert settings.snapshot() == previous_snapshot
    assert config.config_path.read_bytes() == previous_document
    assert config.get_raw("defaults")["live_stream"]["analytics-mqtt"]["broker_port"] == 1883
    assert set(config.config_path.parent.iterdir()) == previous_files
    assert changes == []
    assert dialog._apply()
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
    editor = next(editor for field, editor in dialog._configuration_editors.items() if field.path == path)
    edit = editor._text
    assert edit is not None
    assert edit.echoMode() == QLineEdit.EchoMode.Normal
    assert edit.displayText() == reference

    edit.setText("synthetic-literal-secret")
    assert edit.echoMode() == QLineEdit.EchoMode.Password
    assert edit.displayText() != edit.text()
    assert editor.text() == "synthetic-literal-secret"

    edit.setText("~$not a reference")
    assert edit.echoMode() == QLineEdit.EchoMode.Password
    assert dialog._apply()
    root, *keys = path.split(".")
    saved: Any = config.get(root)
    for key in keys:
        saved = saved[key]
    assert saved == "~$not a reference"
    edit.setText("$REPLACEMENT_SECRET")
    assert edit.echoMode() == QLineEdit.EchoMode.Normal
    assert edit.displayText() == "$REPLACEMENT_SECRET"
    assert dialog._apply()
    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    restored = next(editor for field, editor in reopened._configuration_editors.items() if field.path == path)
    assert restored._text is not None
    assert restored._text.displayText() == "$REPLACEMENT_SECRET"


@pytest.mark.parametrize(
    "path,resolved",
    [
        ("defaults.live_stream.rtsp.camera_head", None),
        ("defaults.live_stream.analytics-mqtt.broker_port", "not-a-number"),
        ("defaults.live_stream.analytics-websocket.channel_id", "0"),
        ("defaults.live_stream.analytics-mqtt.broker_port", "99999"),
        ("defaults.live_stream.rtsp.camera_head", "1883"),
        ("defaults.live_stream.analytics-mqtt.broker_port", "1883"),
        ("defaults.live_stream.analytics-websocket.channel_id", "1883"),
    ],
)
def test_numeric_environment_references_validate_before_saving(
    qtbot: QtBot, config: ConfigManager, monkeypatch: pytest.MonkeyPatch, path: str, resolved: str | None
) -> None:
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    reference = "$AX_TEST_NUMERIC_DEFAULT"
    monkeypatch.delenv(reference[1:], raising=False)
    if resolved is not None:
        monkeypatch.setenv(reference[1:], resolved)
    settings = GlobalSettings()
    settings.load_from_config(config)
    config.save()
    previous = config.config_path.read_bytes()
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    editor = next(editor for field, editor in dialog._configuration_editors.items() if field.path == path)
    assert editor._text is not None
    editor._text.setText(reference)
    if resolved == "1883":
        assert dialog._apply()
        saved: Any = json.loads(config.config_path.read_text())
        for key in path.split("."):
            saved = saved[key]
        assert saved == reference
    else:
        assert not dialog._apply()
        assert config.config_path.read_bytes() == previous


@pytest.mark.parametrize("resolved", [None, "", "   ", "folder"])
def test_storage_environment_references_validate_before_saving(
    qtbot: QtBot,
    config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolved: str | None,
) -> None:
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    reference = "$AX_TEST_STORAGE_DEFAULT"
    monkeypatch.delenv(reference[1:], raising=False)
    if resolved is not None:
        monkeypatch.setenv(reference[1:], str(tmp_path / "new-cache") if resolved == "folder" else resolved)
    settings = GlobalSettings()
    settings.load_from_config(config)
    config.activate_storage()
    active_storage = config.get("storage")
    config.save()
    previous = config.config_path.read_bytes()
    changes: list[tuple[str, Any]] = []
    settings.setting_changed.connect(lambda key, value: changes.append((key, value)))
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog._theme_combo.setCurrentIndex(dialog._theme_combo.findData("dark"))
    editor = next(
        editor for field, editor in dialog._configuration_editors.items() if field.path == "storage.cache_dir"
    )
    assert editor._text is not None
    editor._text.setText(reference)
    if resolved == "folder":
        assert dialog._apply()
        assert json.loads(config.config_path.read_text())["storage"]["cache_dir"] == reference
    else:
        assert not dialog._apply()
        assert "non-empty folder path" in dialog._status_label.text()
        assert config.config_path.read_bytes() == previous
        assert changes == []
    assert config.get("storage") == active_storage


def test_settings_dialog_marks_restart_only_settings_one_way(qtbot: QtBot) -> None:
    """Restart-only settings share one marker, and no other text talks about restarting."""
    from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel

    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    texts = [
        *(label.text() for label in dialog.findChildren(QLabel)),
        *(checkbox.text() for checkbox in dialog.findChildren(QCheckBox)),
        *(group.title() for group in dialog.findChildren(QGroupBox)),
    ]
    marker = "(requires restart)"
    marked = {text.removesuffix(f" {marker}") for text in texts if text.endswith(marker)}
    assert marked == {"Use the app title bar", "Graphics acceleration", "Storage locations"}
    assert not [text for text in texts if "restart" in text.lower() and not text.endswith(marker)]
    assert "Shortcut changes apply when you click OK in the shortcut editor, even if you cancel Settings." in texts


def test_saving_a_restart_setting_asks_to_restart_now(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a saved change marked (requires restart) asks; the answer is left for the main window to act on."""
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    GlobalSettings.reset_instance()
    try:
        asked: list[bool] = []

        def ask_to_restart(self: SettingsDialog) -> bool:
            asked.append(True)
            return True

        monkeypatch.setattr(SettingsDialog, "_ask_to_restart", ask_to_restart)

        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        dialog._theme_combo.setCurrentIndex(dialog._theme_combo.findData("light"))
        dialog._on_ok()
        assert asked == [] and not dialog.restart_requested

        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        editor = next(editor for field, editor in dialog._configuration_editors.items() if field.directory)
        assert editor._text is not None
        editor._text.setText(f"  {editor.text()}  ")
        dialog._on_ok()
        assert asked == []  # The same path with spaces saves the same value.

        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        dialog._custom_frame.setChecked(not dialog._custom_frame.isChecked())
        dialog._on_ok()
        assert asked == [True] and dialog.restart_requested
        assert not any(button.text() == "Apply" for button in dialog.findChildren(QPushButton))
    finally:
        GlobalSettings.reset_instance()


def test_restart_relaunches_the_same_command_only_after_the_app_has_saved(
    monkeypatch: pytest.MonkeyPatch, qapp: QApplication, caplog: pytest.LogCaptureFixture
) -> None:
    """Restart Now only quits; the app relaunches itself after its exit-time saves, once, and reports a failure."""
    from ax_devil.modules.application_shell import restart

    started: list[tuple[str, list[str]]] = []
    results = [(True, 42), (False, -1)]

    def start_detached(program: str, arguments: list[str]) -> tuple[bool, int]:
        started.append((program, arguments))
        return results.pop(0)

    monkeypatch.setattr(sys, "orig_argv", ["/usr/bin/python3", "-I", "-m", "ax_devil.cli"])
    monkeypatch.setattr(sys, "argv", ["/path/ax_devil/cli.py"])
    monkeypatch.setattr(QProcess, "startDetached", start_detached)
    monkeypatch.setattr(QApplication, "closeAllWindows", lambda: None)
    monkeypatch.setattr(QApplication, "quit", lambda: None)

    restart.relaunch_if_requested(qapp, [])
    assert started == []  # No restart was asked for.

    closed: list[str] = []

    class _MainWindow(QWidget):
        def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
            closed.append("main")
            super().closeEvent(event)

    main_window = _MainWindow()
    restart.restart_application(main_window)
    assert closed == ["main"]  # The main window cleans up even if another window refuses to close.
    assert started == []  # Nothing starts before the app has saved on exit.
    restart.relaunch_if_requested(qapp, [])
    restart.relaunch_if_requested(qapp, [])
    assert started == [("/usr/bin/python3", ["-I", "-m", "ax_devil.cli"])]

    restart.restart_application(main_window)
    restart.relaunch_if_requested(qapp, [])
    assert "Could not restart ax-devil" in caplog.text
