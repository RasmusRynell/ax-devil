"""Tests for the settings that need a restart and the restart itself."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from tests.helpers.settings_dialog import (
    answer_restart_prompt,
    checkbox,
    choice,
    choose,
    click_ok,
    preference_input,
)


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


def test_saving_a_restart_setting_asks_to_restart_now(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a saved change marked (requires restart) asks; the answer is left for the main window to act on."""
    from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

    asked = answer_restart_prompt(monkeypatch, "Restart Now")

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    choose(choice(dialog, "Theme"), "light")
    assert click_ok(dialog)
    assert asked == [] and not dialog.restart_requested

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    folder = preference_input(dialog, "storage.cache_dir")
    folder.setText(f"  {folder.text()}  ")
    assert click_ok(dialog)
    assert asked == []  # The same path with spaces saves the same value.

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    title_bar = checkbox(dialog, "Use the app title bar (requires restart)")
    title_bar.setChecked(not title_bar.isChecked())
    assert click_ok(dialog)
    assert len(asked) == 1 and dialog.restart_requested


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
