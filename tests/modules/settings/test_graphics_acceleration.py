"""Test presentation policy, overrides, and the restart-required settings UI."""

from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.settings import GlobalSettings


@pytest.mark.parametrize("mode", list(GraphicsAcceleration))
def test_presentation_policy(mode: GraphicsAcceleration, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("QT_WIDGETS_RHI", raising=False)
    monkeypatch.delenv("QT_QUICK_BACKEND", raising=False)
    mode.configure()
    assert os.environ.get("QT_QUICK_BACKEND") == ("software" if mode is GraphicsAcceleration.OFF else None)
    expected = {
        GraphicsAcceleration.AUTO: "1",
        GraphicsAcceleration.OFF: "0",
    }[mode]
    assert os.environ.get("QT_WIDGETS_RHI") == expected


@pytest.mark.parametrize("mode", list(GraphicsAcceleration))
@pytest.mark.parametrize("override", ["0", "1"])
def test_environment_override_wins(mode: GraphicsAcceleration, override: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QT_QUICK_BACKEND", "software")
    monkeypatch.setenv("QT_WIDGETS_RHI", override)
    monkeypatch.setenv("QT_WIDGETS_RHI_BACKEND", "vulkan")
    mode.configure()
    assert os.environ["QT_QUICK_BACKEND"] == "software"
    assert os.environ["QT_WIDGETS_RHI"] == override
    assert os.environ["QT_WIDGETS_RHI_BACKEND"] == "vulkan"


@pytest.mark.parametrize("invalid", ["unknown", None, True, {}, []])
def test_invalid_saved_mode_uses_auto(invalid: object) -> None:
    assert GraphicsAcceleration.from_config(invalid) is GraphicsAcceleration.AUTO


def test_dialog_cancel_apply_and_reopen(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QT_WIDGETS_RHI", raising=False)
    GlobalSettings.reset_instance()
    try:
        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        dialog._acceleration_combo.setCurrentIndex(dialog._acceleration_combo.findData("off"))
        assert "troubleshooting" in dialog._acceleration_description.text()
        dialog.reject()
        assert GlobalSettings().graphics_acceleration is GraphicsAcceleration.AUTO

        applied = SettingsDialog()
        qtbot.addWidget(applied)
        applied._acceleration_combo.setCurrentIndex(applied._acceleration_combo.findData("off"))
        assert applied._apply(), applied._status_label.text()
        assert GlobalSettings().graphics_acceleration is GraphicsAcceleration.OFF
        assert "QT_WIDGETS_RHI" not in os.environ
        applied._acceleration_combo.setCurrentIndex(applied._acceleration_combo.findData("auto"))
        applied.reject()
        assert GlobalSettings().graphics_acceleration is GraphicsAcceleration.OFF

        reopened = SettingsDialog()
        qtbot.addWidget(reopened)
        assert reopened._acceleration_combo.currentData() == "off"
        reopened._acceleration_combo.setCurrentIndex(reopened._acceleration_combo.findData("auto"))
        reopened._on_ok()
        assert GlobalSettings().graphics_acceleration is GraphicsAcceleration.AUTO
    finally:
        GlobalSettings.reset_instance()


def test_dialog_explains_external_override(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QT_WIDGETS_RHI", "0")
    GlobalSettings.reset_instance()
    try:
        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        assert any("QT_WIDGETS_RHI=0" in label.text() for label in dialog.findChildren(QLabel))
    finally:
        GlobalSettings.reset_instance()


def test_app_default_is_not_shown_as_external_override(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("QT_WIDGETS_RHI", raising=False)
    GlobalSettings.reset_instance()
    try:
        settings = GlobalSettings()
        settings.graphics_acceleration.configure()
        assert os.environ["QT_WIDGETS_RHI"] == "1"
        assert settings.graphics_acceleration_override is None

        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        assert not any("environment override" in label.text() for label in dialog.findChildren(QLabel))
    finally:
        GlobalSettings.reset_instance()
