from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.plugin_system import (
    PluginDefinitionBase,
    PluginRecord,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager


def test_startup_reports_failed_plugins(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed plug-ins are listed with their error in one dialog."""
    manager = ShortcutManager()
    manager.register_defaults()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    failed = PluginRecord(
        definition=PluginDefinitionBase(plugin_type="decoder", plugin_id="old-plugin", display_name="Old"),
        entrypoint=Path("/plugins/old"),
        origin="package:old",
        status=PluginStatus.FAILED,
        error="must explicitly declare scene_model_version",
    )
    monkeypatch.setattr(RuntimePluginRegistry, "list_plugins", classmethod(lambda cls: [failed]))
    shown: list[str] = []

    def record_exec(dialog: BaseDialog) -> int:
        shown.extend(label.text() for label in dialog.findChildren(QLabel))
        return 0

    monkeypatch.setattr(BaseDialog, "exec", record_exec)

    window.report_failed_plugins()

    assert "old-plugin: must explicitly declare scene_model_version" in shown
