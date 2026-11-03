"""User-facing selector controls not driven by the render catalog state model."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QPushButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_viewer.media_tools import SceneRenderCatalogSelector
from tests.catalog_helpers import create_catalog, wait_for_catalogs


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
    qtbot.waitUntil(lambda: combo.findText("Vehicle Review") >= 0, timeout=10_000)
    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
        combo.setCurrentText("Vehicle Review")

    assert selection.active_catalog_path() == catalog_file.path
    assert selector.active_catalog is selection.active_catalog()


def test_reload_button_picks_up_edits_to_the_active_catalog(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    selection = render_catalog_manager.create_selection()
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    active_path = create_catalog(
        qtbot, render_catalog_manager, "Vehicle Review", base_catalog_path=small_catalog_path
    ).path
    selection.select_catalog(active_path)
    wait_for_catalogs(qtbot, render_catalog_manager)
    before = selection.active_catalog()
    assert before is not None

    document = json.loads(active_path.read_text(encoding="utf-8"))
    document["metadata"]["name"] = "Vehicle Review Edited"
    active_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    reload_button = selector.findChild(QPushButton, "reloadRenderCatalogButton")
    assert reload_button is not None
    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
        reload_button.click()

    after = selection.active_catalog()
    assert after is not None
    assert after.content_hash != before.content_hash


def test_apply_to_all_names_the_catalogs_other_open_views_use_until_they_close(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    other_path = create_catalog(
        qtbot, render_catalog_manager, "Vehicle Review", base_catalog_path=small_catalog_path
    ).path
    selection = render_catalog_manager.create_selection()
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    other = render_catalog_manager.create_selection()
    wait_for_catalogs(qtbot, render_catalog_manager)
    apply_button = selector.findChild(QPushButton, "applyRenderCatalogToAllButton")
    others = selector.findChild(ElidedLabel, "renderCatalogOthersLabel")
    assert apply_button is not None and others is not None
    assert not apply_button.isVisibleTo(selector)

    other.select_catalog(other_path)
    wait_for_catalogs(qtbot, render_catalog_manager)

    assert apply_button.isVisibleTo(selector) and apply_button.text() == "Apply to all 2"
    assert others.full_text() == "Other views use: Vehicle Review"
    other.cleanup()
    assert not apply_button.isVisibleTo(selector)
    assert render_catalog_manager.catalogs_in_use.paths() == (selection.active_catalog_path(),)
