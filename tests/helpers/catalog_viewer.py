"""Opening the catalog viewer window and reading its parts in tests."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.catalog_viewer import CatalogViewerWindow
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from tests.catalog_helpers import wait_for_catalogs


def open_viewer(qtbot: QtBot, manager: SceneRenderCatalogManager, path: Path) -> CatalogViewerWindow:
    window = CatalogViewerWindow(manager)
    qtbot.addWidget(window)
    window.show_catalog(path)
    window.show()
    wait_for_catalogs(qtbot, manager)
    return window


def file_changed(qtbot: QtBot, manager: SceneRenderCatalogManager, path: Path) -> None:
    """Look at the files again, then send the notice the file watcher sends for *path*, and let the viewer settle."""
    listed: list[object] = []
    manager.refresh_catalogs(on_done=listed.append)
    qtbot.waitUntil(lambda: bool(listed), timeout=10_000)
    manager.catalogFileChanged.emit(path)
    wait_for_catalogs(qtbot, manager)


def relabel_person(path: Path, label: str, source: str | None = None) -> None:
    document = json.loads(source if source is not None else path.read_text(encoding="utf-8"))
    person = next(recipe for recipe in document["recipes"]["classifications"] if recipe["id"] == "human")
    person["label"] = label
    path.write_text(json.dumps(document), encoding="utf-8")


def error_banner(window: CatalogViewerWindow) -> QLabel:
    banner = window.findChild(QLabel, "catalogViewerError")
    assert banner is not None
    return banner
