"""Test presentation policy, overrides, and the restart-required settings UI."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.settings import GlobalSettings
from tests.helpers.settings_dialog import answer_restart_prompt, choice, choose, click_ok, save_error

ACCELERATION = "Graphics acceleration (requires restart)"


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    GlobalSettings.reset_instance()
    yield
    GlobalSettings.reset_instance()


@pytest.mark.parametrize("mode", list(GraphicsAcceleration))
def test_presentation_policy(mode: GraphicsAcceleration, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("QT_WIDGETS_RHI", raising=False)
    monkeypatch.delenv("QT_QUICK_BACKEND", raising=False)
    mode.configure()
    assert os.environ.get("QT_QUICK_BACKEND") == ("software" if mode is GraphicsAcceleration.OFF else None)
    expected = {
        GraphicsAcceleration.AUTO: None,
        GraphicsAcceleration.OFF: "0",
    }[mode]
    assert os.environ.get("QT_WIDGETS_RHI") == expected


def test_environment_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QT_QUICK_BACKEND", "rhi")
    monkeypatch.setenv("QT_WIDGETS_RHI", "1")
    GraphicsAcceleration.OFF.configure()
    assert os.environ["QT_QUICK_BACKEND"] == "rhi"
    assert os.environ["QT_WIDGETS_RHI"] == "1"


@pytest.mark.parametrize("invalid", ["unknown", {}])
def test_invalid_saved_mode_uses_auto(invalid: object) -> None:
    assert GraphicsAcceleration.from_config(invalid) is GraphicsAcceleration.AUTO


def test_dialog_cancel_ok_and_reopen(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The choice is saved on OK, discarded on Cancel, and only takes effect at the next start."""
    monkeypatch.delenv("QT_WIDGETS_RHI", raising=False)
    asked = answer_restart_prompt(monkeypatch, "Later")
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    choose(choice(dialog, ACCELERATION), "off")
    dialog.reject()
    assert GlobalSettings().graphics_acceleration is GraphicsAcceleration.AUTO

    saved_dialog = SettingsDialog()
    qtbot.addWidget(saved_dialog)
    choose(choice(saved_dialog, ACCELERATION), "off")
    assert click_ok(saved_dialog), save_error(saved_dialog)
    assert GlobalSettings().graphics_acceleration is GraphicsAcceleration.OFF
    assert len(asked) == 1 and not saved_dialog.restart_requested
    assert "QT_WIDGETS_RHI" not in os.environ

    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    assert choice(reopened, ACCELERATION).currentData() == "off"


@pytest.mark.parametrize("inherited", ["0", None])
def test_dialog_explains_only_an_inherited_override(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, inherited: str | None
) -> None:
    """A user's QT_WIDGETS_RHI is explained; the app's own startup default for Off is not."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("QT_WIDGETS_RHI", raising=False)
    if inherited is not None:
        monkeypatch.setenv("QT_WIDGETS_RHI", inherited)
    settings = GlobalSettings()
    settings.graphics_acceleration = GraphicsAcceleration.OFF
    settings.graphics_acceleration.configure()
    assert os.environ["QT_WIDGETS_RHI"] == "0"

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    explained = any("QT_WIDGETS_RHI" in label.text() for label in dialog.findChildren(QLabel))
    assert explained is (inherited is not None)
