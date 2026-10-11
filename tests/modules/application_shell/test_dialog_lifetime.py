"""Temporary application dialogs must not accumulate under the main window."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.about_dialog import AboutDialog
from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from tests.helpers.shortcuts import make_shortcut_manager


@pytest.fixture
def window(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> MainWindow:
    """Build a real main window using isolated configuration and catalogs."""
    shortcuts = make_shortcut_manager()
    window = MainWindow(shortcut_manager=shortcuts, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    return window


@pytest.mark.parametrize(
    "action_name",
    ["_on_keyboard_shortcuts", "_on_settings", "_on_add_video", "_on_add_live_stream", "_on_add_playlist"],
)
def test_repeated_modal_dialogs_are_destroyed(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, action_name: str
) -> None:
    """Repeatedly cancelling a real modal dialog releases every instance and its children."""
    original_exec = BaseDialog.exec
    destroyed: list[str] = []

    def cancel(dialog: BaseDialog) -> int:
        dialog.destroyed.connect(lambda: destroyed.append(action_name))
        QTimer.singleShot(0, dialog.reject)
        return original_exec(dialog)

    monkeypatch.setattr(BaseDialog, "exec", cancel)
    for _ in range(3):
        getattr(window, action_name)()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(destroyed) == 3
    assert window.findChildren(BaseDialog) == []


def test_dialog_is_disposed_when_launch_fails(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception during a modal launch cannot retain its dialog under the main window."""
    destroyed: list[bool] = []

    def fail(dialog: BaseDialog) -> int:
        dialog.destroyed.connect(lambda: destroyed.append(True))
        raise ValueError("launch failed")

    monkeypatch.setattr(BaseDialog, "exec", fail)
    with pytest.raises(ValueError, match="launch failed"):
        window._on_settings()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert destroyed == [True]
    assert window.findChildren(BaseDialog) == []


def test_about_dialog_is_destroyed_after_close(window: MainWindow) -> None:
    """The nonblocking About dialog is released, and reopening after a close opens a new one."""
    for _ in range(3):
        window._on_about()
        dialog = window.findChild(AboutDialog)
        assert dialog is not None and dialog.isVisible()
        dialog.accept()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert window.findChildren(AboutDialog) == []
