from __future__ import annotations

from PySide6.QtGui import QAction
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.plugin_manager.plugins_view import PluginsView
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from tests.helpers.shortcuts import make_shortcut_manager


def test_the_plugins_entry_shows_the_plugins_view(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    window = MainWindow(shortcut_manager=make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    window.show()
    view = window.findChild(PluginsView)
    assert view is not None and not view.isVisible()

    next(action for action in window.findChildren(QAction) if action.text() == "Plugins").trigger()

    assert view.isVisible()
