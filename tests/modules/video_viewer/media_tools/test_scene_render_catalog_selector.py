from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QToolButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogManager,
    SceneRenderCatalogSelection,
    SceneRenderCatalogStore,
    create_scene_render_catalog_manager,
)
from ax_devil.modules.video_viewer.media_tools import SceneRenderCatalogSelector


def test_selector_requests_catalog_management(qtbot: QtBot, tmp_path: Path) -> None:
    selector = SceneRenderCatalogSelector(_selection(tmp_path))
    qtbot.addWidget(selector)

    open_button = selector.findChild(QToolButton, "openRenderCatalogsButton")
    assert open_button is not None

    with qtbot.waitSignal(selector.catalogViewerRequested):
        QTest.mouseClick(open_button, Qt.MouseButton.LeftButton)


def test_selector_selects_catalog_through_manager(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    combo = _combo(selector)

    with qtbot.waitSignal(selection.activeCatalogChanged):
        combo.setCurrentText("Vehicle Review")

    assert selection.active_catalog_path() == catalog_file.path
    assert selector.active_catalog is selection.active_catalog()
    assert _status(selector).text() == "Selected catalog: user.vehicle-review"


def test_selector_reload_button_reloads_manager_catalog(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    active_path = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path).path
    selection.select_catalog(active_path)
    before = selection.active_catalog()
    assert before is not None

    document = json.loads(active_path.read_text(encoding="utf-8"))
    document["metadata"]["name"] = "Vehicle Review Edited"
    active_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    reload_button = selector.findChild(QPushButton, "reloadRenderCatalogButton")
    assert reload_button is not None
    with qtbot.waitSignal(selection.activeCatalogChanged):
        reload_button.click()

    after = selection.active_catalog()
    assert after is not None
    assert after.content_hash != before.content_hash
    assert _status(selector).text() == "Render catalog reloaded."


def test_selector_does_not_auto_refresh_before_popup(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    combo = _combo(selector)
    built_ins = len(manager.catalog_store.built_in_catalog_paths)
    assert combo.count() == built_ins

    manager.catalog_store.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    combo.showPopup()
    combo.hidePopup()

    assert combo.count() == built_ins

    selector.refresh_catalogs()

    assert combo.count() == built_ins + 1
    assert combo.itemText(built_ins) == "Vehicle Review"


def _manager(tmp_path: Path) -> SceneRenderCatalogManager:
    store = SceneRenderCatalogStore(tmp_path)
    return create_scene_render_catalog_manager(catalog_store=store)


def _selection(tmp_path: Path) -> SceneRenderCatalogSelection:
    return _manager(tmp_path).create_selection()


def _combo(selector: SceneRenderCatalogSelector) -> QComboBox:
    combo = selector.findChild(QComboBox, "renderCatalogCombo")
    assert combo is not None
    return combo


def _status(selector: SceneRenderCatalogSelector) -> QLabel:
    status = selector.findChild(QLabel, "renderCatalogStatus")
    assert status is not None
    return status
