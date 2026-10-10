from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.chrome import BaseDialog, ChromeWindow, window_uses_custom_frame
from ax_devil.modules.diagnostics.debug_window import DebugWindow
from ax_devil.modules.diagnostics.plugin_window import PluginWindow
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.shortcuts.shortcuts_dialog import ShortcutsDialog


def _direct_window_border(window: QWidget) -> QWidget | None:
    """Return the direct custom-frame border child for a window."""
    return window.findChild(QWidget, "AxDevilWindowBorder", Qt.FindChildOption.FindDirectChildrenOnly)


def _make_shortcut_manager() -> ShortcutManager:
    """Return a registered shortcut manager for MainWindow tests."""
    manager = ShortcutManager()
    manager.register_defaults()
    return manager


@pytest.mark.parametrize("custom_frame", [True, False])
def test_child_chrome_window_honors_explicit_frame_choice(qtbot: QtBot, custom_frame: bool) -> None:
    parent = ChromeWindow(use_custom_frame=True)
    child = ChromeWindow(parent=parent, use_custom_frame=custom_frame, show_custom_frame_border=custom_frame)
    qtbot.addWidget(parent)
    assert (child.findChild(QWidget, "AxDevilTitleBar") is not None) is custom_frame
    assert (_direct_window_border(child) is not None) is custom_frame


@pytest.mark.parametrize("custom_frame", [True, False])
def test_dialog_and_tool_windows_share_parent_chrome_policy(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, custom_frame: bool
) -> None:
    window = MainWindow(
        shortcut_manager=_make_shortcut_manager(),
        render_catalog_manager=render_catalog_manager,
        use_custom_frame=custom_frame,
    )
    qtbot.addWidget(window)
    dialog = BaseDialog(parent=window, title="Shared Chrome")
    debug_window = DebugWindow(parent=window, use_custom_frame=window_uses_custom_frame(window))
    plugin_window = PluginWindow(parent=window, use_custom_frame=window_uses_custom_frame(window))
    assert not window.shows_custom_frame_border
    assert _direct_window_border(window) is None
    for child in (dialog, debug_window, plugin_window):
        assert bool(child.windowFlags() & Qt.WindowType.FramelessWindowHint) is custom_frame
        assert child.shows_custom_frame_border is custom_frame
        assert (child.findChild(QWidget, "AxDevilTitleBar") is not None) is custom_frame
        assert (_direct_window_border(child) is not None) is custom_frame


@pytest.mark.parametrize("custom_frame", [True, False])
def test_shortcut_editor_opened_from_settings_inherits_chrome(qtbot: QtBot, custom_frame: bool) -> None:
    """A nested dialog preserves the same frame policy through its dialog parent."""
    parent = ChromeWindow(use_custom_frame=custom_frame)
    qtbot.addWidget(parent)
    settings = SettingsDialog(parent)
    shortcuts = ShortcutsDialog(_make_shortcut_manager(), settings)
    assert bool(shortcuts.windowFlags() & Qt.WindowType.FramelessWindowHint) is custom_frame
    assert (shortcuts.findChild(QWidget, "AxDevilTitleBar") is not None) is custom_frame
    assert (_direct_window_border(shortcuts) is not None) is custom_frame
