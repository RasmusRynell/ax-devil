"""User-facing selector controls not driven by the render catalog state model."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QPushButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_viewer.media_tools import SceneRenderCatalogSelector


def test_catalog_created_after_opening_can_be_picked_from_the_list(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    selection = render_catalog_manager.create_selection()
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    combo = selector.findChild(QComboBox, "renderCatalogCombo")
    assert combo is not None
    catalog_file = render_catalog_manager.catalog_store.create_catalog(
        "Vehicle Review", base_catalog_path=small_catalog_path
    )

    selector.refresh_catalogs()
    with qtbot.waitSignal(selection.activeCatalogChanged):
        combo.setCurrentText("Vehicle Review")

    assert selection.active_catalog_path() == catalog_file.path
    assert selector.active_catalog is selection.active_catalog()


def test_reload_button_picks_up_edits_to_the_active_catalog(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    selection = render_catalog_manager.create_selection()
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    active_path = render_catalog_manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path).path
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
