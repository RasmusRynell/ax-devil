"""Tests for GlobalSettings."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
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
        for preference in OverlayPreference:
            settings.set_overlay_enabled(preference, False)
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

        settings.apply_snapshot({"overlay_interaction": {"hover_enabled": True}})
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
    settings.apply_snapshot({"appearance": {"graphics_acceleration": "off"}})
    settings.apply_snapshot({"appearance": {"graphics_acceleration": "off"}})
    assert changes == [("appearance.graphics_acceleration", "off")]
    assert settings.graphics_acceleration is GraphicsAcceleration.OFF
    assert "QT_WIDGETS_RHI" not in os.environ
    settings.save_to_config(config)
    GlobalSettings.reset_instance()
    restored = GlobalSettings()
    restored.load_from_config(config)
    assert restored.graphics_acceleration is GraphicsAcceleration.OFF


def test_theme_dialog_cancel_apply_and_config_round_trip(qtbot: QtBot, config: ConfigManager) -> None:
    """Appearance changes commit on Apply/OK and retain unrelated raw UI settings."""
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
    assert settings.snapshot()["appearance"]["theme"] == "dark"
    assert changes == []

    applied = SettingsDialog()
    qtbot.addWidget(applied)
    applied._theme_combo.setCurrentIndex(applied._theme_combo.findData("light"))
    applied._on_apply()
    applied._on_apply()
    assert changes == ["light"]
    applied._theme_combo.setCurrentIndex(applied._theme_combo.findData("dark"))
    applied.reject()
    assert settings.theme == ThemeMode.LIGHT
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
    assert settings.snapshot()["appearance"]["theme"] == "auto"
    assert changes == ["light", "auto"]


@pytest.mark.parametrize("invalid", ["unknown", None, True, {}, []])
def test_invalid_theme_follows_system(invalid: object) -> None:
    assert ThemeMode.from_config(invalid) is ThemeMode.AUTO


@pytest.mark.parametrize("invalid", ["huge", None, 14, {}])
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

    edit.setText("$not a reference")
    assert edit.echoMode() == QLineEdit.EchoMode.Password
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
    "path",
    [
        "defaults.live_stream.rtsp.camera_head",
        "defaults.live_stream.analytics-mqtt.broker_port",
        "defaults.live_stream.analytics-websocket.channel_id",
    ],
)
@pytest.mark.parametrize("resolved", [None, "not-a-number", "0", "99999", "1883"])
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
    """Restart-only settings, and only those, carry the Restart badge beside their name."""
    from PySide6.QtWidgets import QAbstractButton, QLabel

    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    marked = set()
    for badge in dialog.findChildren(QLabel, "RestartBadge"):
        row = badge.parentWidget()
        assert row is not None
        named = next(
            child for child in row.findChildren(QLabel) + row.findChildren(QAbstractButton) if child is not badge
        )
        marked.add(named.text())
    assert marked == {"Use the app title bar", "Graphics acceleration", "Storage locations"}
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert not [text for text in texts if "restart" in text.lower() and text != "Restart"]
    assert "Shortcut changes apply when you click OK in the shortcut editor, even if you cancel Settings." in texts
