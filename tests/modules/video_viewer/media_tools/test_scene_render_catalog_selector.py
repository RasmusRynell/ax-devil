from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QLabel, QToolButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogManager,
    SceneRenderCatalogSelection,
    SceneRenderCatalogStore,
    create_scene_render_catalog_manager,
)
from ax_devil.modules.video_viewer.media_tools import SceneRenderCatalogSelector


def test_selector_displays_manager_default_catalog(qtbot: QtBot, tmp_path: Path) -> None:
    selection = _selection(tmp_path)
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)

    combo = _combo(selector)

    assert combo.currentText() == "Standard (default)"
    assert selector.active_catalog is selection.active_catalog()
    assert _status(selector).text() == "Selected catalog: built-in.standard"
    assert not _action(selector, "useRenderCatalogAsDefaultAction").isEnabled()


def test_selector_uses_active_catalog_as_default(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    selection.select_catalog(catalog_file.path)
    use_as_default = _action(selector, "useRenderCatalogAsDefaultAction")
    assert use_as_default.isEnabled()

    use_as_default.trigger()

    assert manager.default_catalog_path() == catalog_file.path
    assert _combo(selector).currentText() == "Vehicle Review (default)"
    assert not use_as_default.isEnabled()


def test_selector_disables_catalog_actions_after_failed_reload(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    selection.select_catalog(catalog_file.path)
    document = json.loads(catalog_file.path.read_text(encoding="utf-8"))
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    catalog_file.path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")
    selector.reload_selected_catalog()
    use_as_default = _action(selector, "useRenderCatalogAsDefaultAction")

    manager.refresh_catalogs()
    use_as_default.trigger()

    assert "Render catalog error" in _status(selector).text()
    assert not use_as_default.isEnabled()
    assert not _action(selector, "applyRenderCatalogToAllAction").isEnabled()
    assert manager.default_catalog_path() == manager.built_in_catalog_path()


def test_selector_records_failed_use_as_default_for_catalog_broken_since_loading(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    selection.select_catalog(catalog_file.path)
    catalog_file.path.write_text("{ broken", encoding="utf-8")

    use_as_default = _action(selector, "useRenderCatalogAsDefaultAction")
    use_as_default.trigger()
    manager.refresh_catalogs()

    assert _status(selector).text().startswith("Active render catalog is invalid:")
    assert not selection.active_catalog_loaded()
    assert not use_as_default.isEnabled()
    assert manager.default_catalog_path() == manager.built_in_catalog_path()


def test_selector_disables_catalog_actions_until_failed_selection_is_repaired(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    good_text = catalog_file.path.read_text(encoding="utf-8")
    document = json.loads(good_text)
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    catalog_file.path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    apply_to_all = _action(selector, "applyRenderCatalogToAllAction")
    use_as_default = _action(selector, "useRenderCatalogAsDefaultAction")

    _combo(selector).setCurrentText("Vehicle Review")

    assert _combo(selector).currentData() == str(catalog_file.path)
    assert "Render catalog error" in _status(selector).text()
    assert not apply_to_all.isEnabled()
    assert not use_as_default.isEnabled()

    catalog_file.path.write_text(good_text, encoding="utf-8")
    selector.reload_selected_catalog()

    assert _status(selector).text() == "Render catalog reloaded."
    assert apply_to_all.isEnabled()
    assert use_as_default.isEnabled()


def test_selector_recovers_after_repairing_catalog_listed_as_invalid(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)
    _combo(selector).setCurrentText("Vehicle Review")
    good_text = catalog_file.path.read_text(encoding="utf-8")
    catalog_file.path.write_text("{ broken", encoding="utf-8")
    selector.refresh_catalogs()
    assert _combo(selector).currentText() == "vehicle-review.json (invalid)"
    assert not _action(selector, "applyRenderCatalogToAllAction").isEnabled()
    assert not _action(selector, "useRenderCatalogAsDefaultAction").isEnabled()

    catalog_file.path.write_text(good_text, encoding="utf-8")
    selector.reload_selected_catalog()

    assert _combo(selector).currentText() == "Vehicle Review"
    assert _action(selector, "applyRenderCatalogToAllAction").isEnabled()
    assert _action(selector, "useRenderCatalogAsDefaultAction").isEnabled()


def test_selector_applies_active_catalog_to_all_selections(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _manager(tmp_path)
    first_selection = manager.create_selection()
    second_selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    selector = SceneRenderCatalogSelector(first_selection)
    qtbot.addWidget(selector)
    first_selection.select_catalog(catalog_file.path)

    with qtbot.waitSignal(second_selection.activeCatalogChanged):
        _action(selector, "applyRenderCatalogToAllAction").trigger()

    assert second_selection.active_catalog_path() == catalog_file.path
    assert manager.default_catalog_path() == manager.built_in_catalog_path()


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


def test_selector_records_failed_apply_to_all_without_changing_other_selections(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _manager(tmp_path)
    first_selection = manager.create_selection()
    second_selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    first_selector = SceneRenderCatalogSelector(first_selection)
    second_selector = SceneRenderCatalogSelector(second_selection)
    qtbot.addWidget(first_selector)
    qtbot.addWidget(second_selector)
    first_selection.select_catalog(catalog_file.path)
    document = json.loads(catalog_file.path.read_text(encoding="utf-8"))
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    catalog_file.path.write_text(json.dumps(document), encoding="utf-8")
    apply_to_all = _action(first_selector, "applyRenderCatalogToAllAction")

    apply_to_all.trigger()
    manager.refresh_catalogs()

    assert _status(first_selector).text() == "Render catalog error: $: Unknown template: missing."
    assert not first_selection.active_catalog_loaded()
    assert not apply_to_all.isEnabled()
    assert second_selection.active_catalog_path() == manager.built_in_catalog_path()
    assert _combo(second_selector).currentData() == str(manager.built_in_catalog_path())
    assert _status(second_selector).text() == "Selected catalog: built-in.standard"


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

    with qtbot.waitSignal(selection.activeCatalogChanged):
        selector.reload_selected_catalog()

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


def test_selector_reports_manager_catalog_error(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    manager = _manager(tmp_path)
    selection = manager.create_selection()
    broken_path = manager.catalog_store.create_catalog("Broken", base_catalog_path=small_catalog_path).path
    document = json.loads(broken_path.read_text(encoding="utf-8"))
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    broken_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")
    manager.refresh_catalogs()
    selector = SceneRenderCatalogSelector(selection)
    qtbot.addWidget(selector)

    _combo(selector).setCurrentText("Broken")

    assert selection.active_catalog_path() == broken_path
    assert "Render catalog error" in _status(selector).text()


def test_selector_selection_does_not_change_other_selection(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _manager(tmp_path)
    first_selection = manager.create_selection()
    second_selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    selector = SceneRenderCatalogSelector(first_selection)
    qtbot.addWidget(selector)

    with qtbot.waitSignal(first_selection.activeCatalogChanged):
        _combo(selector).setCurrentText("Vehicle Review")

    assert first_selection.active_catalog_path() == catalog_file.path
    assert second_selection.active_catalog_path() == manager.default_catalog_path()


def _manager(tmp_path: Path) -> SceneRenderCatalogManager:
    store = SceneRenderCatalogStore(tmp_path)
    return create_scene_render_catalog_manager(catalog_store=store)


def _selection(tmp_path: Path) -> SceneRenderCatalogSelection:
    return _manager(tmp_path).create_selection()


def _combo(selector: SceneRenderCatalogSelector) -> QComboBox:
    combo = selector.findChild(QComboBox, "renderCatalogCombo")
    assert combo is not None
    return combo


def _action(selector: SceneRenderCatalogSelector, name: str) -> QAction:
    action = selector.findChild(QAction, name)
    assert action is not None
    return action


def _status(selector: SceneRenderCatalogSelector) -> QLabel:
    status = selector.findChild(QLabel, "renderCatalogStatus")
    assert status is not None
    return status
