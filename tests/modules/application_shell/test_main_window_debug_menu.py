from __future__ import annotations

import pytest
from PySide6.QtGui import QAction
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.diagnostics.metrics_store import metrics_enabled, set_metrics_enabled
from ax_devil.modules.plugin_manager.plugins_view import PluginsView
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from tests.helpers.shortcuts import make_shortcut_manager


@pytest.mark.parametrize("initially_enabled", [False, True])
def test_collect_debug_metrics_action_is_checked_while_metrics_are_collected(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    initially_enabled: bool,
) -> None:
    """The Debug menu action reads positively: checked means metrics are collected."""
    manager = make_shortcut_manager()
    set_metrics_enabled(initially_enabled)
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    action = next(action for action in window.findChildren(QAction) if action.text() == "Collect Debug Metrics")
    assert action.isChecked() == initially_enabled

    action.trigger()
    assert metrics_enabled() == (not initially_enabled)

    action.trigger()
    assert metrics_enabled() == initially_enabled


def test_the_plugins_entry_shows_the_plugins_view(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    window = MainWindow(shortcut_manager=make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    window.show()
    view = window.findChild(PluginsView)
    assert view is not None and not view.isVisible()

    next(action for action in window.findChildren(QAction) if action.text() == "Plug-ins").trigger()

    assert view.isVisible()
