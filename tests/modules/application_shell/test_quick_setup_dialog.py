"""Tests for Quick Setup, shown on first start."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QAbstractScrollArea, QApplication
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.quick_setup_dialog import QuickSetupDialog
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.chrome.theme import apply_text_size
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.settings.theme_mode import ThemeMode


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """Give each test its own writable configuration document."""
    monkeypatch.setattr(ConfigManager, "_instance", None)
    config = ConfigManager()
    config.set_config_path(tmp_path / "config.json")
    config.set("storage", {key: str(tmp_path / key) for key in config.get_raw("storage")})
    return config


@pytest.fixture(autouse=True)
def settings(config: ConfigManager) -> Iterator[GlobalSettings]:
    """Give each test fresh settings loaded from its own configuration."""
    GlobalSettings.reset_instance()
    settings = GlobalSettings()
    settings.load_from_config(config)
    yield settings
    GlobalSettings.reset_instance()


def _saved_ui(config: ConfigManager) -> dict[str, object]:
    return dict(json.loads(config.config_path.read_text(encoding="utf-8"))["ui"])


def test_setup_stays_pending_until_the_dialog_closes(
    qtbot: QtBot, config: ConfigManager, settings: GlobalSettings
) -> None:
    """A fresh configuration shows Quick Setup on start; closing it records that on disk."""
    assert not settings.quick_setup_done

    with QuickSetupDialog() as dialog:
        qtbot.addWidget(dialog)
        dialog.accept()

    assert settings.quick_setup_done
    assert _saved_ui(config)["quick_setup_done"] is True


def test_choices_apply_live_and_are_saved_on_close(
    qtbot: QtBot, config: ConfigManager, settings: GlobalSettings
) -> None:
    """Clicking an option changes the running app at once; closing in any way saves what is in effect."""
    with QuickSetupDialog() as dialog:
        qtbot.addWidget(dialog)
        dialog.theme_row.select(ThemeMode.DARK)
        dialog.text_size_row.select(TextSize.LARGE)
        assert settings.theme is ThemeMode.DARK
        assert settings.text_size is TextSize.LARGE
        assert _saved_ui(config)["theme"] == "auto"
        dialog.reject()

    saved = _saved_ui(config)
    assert (saved["theme"], saved["text_size"], saved["quick_setup_done"]) == ("dark", "large", True)


@pytest.mark.usefixtures("restore_app_appearance")
def test_larger_text_fits_without_resizing_the_window(qtbot: QtBot, settings: GlobalSettings) -> None:
    """Opened at small text, the dialog already has room for the largest, since window managers may not resize."""
    settings.text_size_changed.connect(lambda size: apply_text_size(size.body_px))
    settings.text_size = TextSize.SMALL
    window = ChromeWindow(use_custom_frame=True)
    qtbot.addWidget(window)
    with QuickSetupDialog(parent=window) as dialog:
        dialog.show()
        qtbot.waitExposed(dialog)
        opened = dialog.size()

        dialog.text_size_row.select(TextSize.LARGER)
        QApplication.processEvents()

        assert dialog.size() == opened
        needed = dialog.sizeHint()
        assert needed.width() <= opened.width() and needed.height() <= opened.height()
        scrollbars = [
            bar
            for area in dialog.findChildren(QAbstractScrollArea)
            for bar in (area.horizontalScrollBar(), area.verticalScrollBar())
        ]
        assert not any(bar.isVisible() for bar in scrollbars)
        dialog.accept()
