from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogLoader,
    SceneRenderCatalogStore,
    create_scene_render_catalog_manager,
)


def test_catalog_manager_starts_selections_on_built_in_catalog(qtbot: QtBot, tmp_path: Path) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()

    assert selection.active_catalog() is not None
    assert selection.active_catalog_path() == manager.built_in_catalog_path()
    assert selection.active_catalog_loaded()
    assert manager.default_catalog_path() == manager.built_in_catalog_path()
    assert sorted(tmp_path.iterdir()) == []
    assert selection.status() == "Selected catalog: built-in.standard"


def test_catalog_revision_shares_selection_cache_and_reloads_document_with_compilation(
    tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    path = manager.create_catalog("Review", base_catalog_path=small_catalog_path).path
    selection = manager.create_selection()
    selection.select_catalog(path)
    first = manager.load_revision(path)

    assert first.catalog is selection.active_catalog()
    assert manager.load_revision(path) is first
    document = json.loads(path.read_text(encoding="utf-8"))
    document["metadata"]["name"] = "Updated review"
    path.write_text(json.dumps(document), encoding="utf-8")
    updated = manager.load_revision(path, force_reload=True)

    assert first.document["metadata"]["name"] == "Review"
    assert updated.document["metadata"]["name"] == "Updated review"
    assert updated.catalog.content_hash == SceneRenderCatalogLoader().validate_document(updated.document).content_hash
    assert updated.catalog is manager.load_catalog(path)


def test_catalog_manager_starts_new_selections_on_default_catalog(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    existing_selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)

    with qtbot.waitSignal(manager.catalogListingChanged):
        manager.set_default_catalog(catalog_file.path)
    new_selection = manager.create_selection()
    restarted_manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))

    assert existing_selection.active_catalog_path() == manager.built_in_catalog_path()
    assert new_selection.active_catalog_path() == catalog_file.path
    assert restarted_manager.create_selection().active_catalog_path() == catalog_file.path


def test_catalog_manager_falls_back_to_built_in_when_default_fails_to_compile(
    tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    broken_path = manager.create_catalog("Broken", base_catalog_path=small_catalog_path).path
    manager.set_default_catalog(broken_path)
    document = json.loads(broken_path.read_text(encoding="utf-8"))
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    broken_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    selection = manager.create_selection()

    assert selection.active_catalog_path() == manager.built_in_catalog_path()
    assert selection.active_catalog() is not None
    assert selection.status().startswith(
        "Using the default built-in catalog because the chosen default could not be used."
    )


def test_catalog_manager_applies_catalog_to_all_selections(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    first_selection = manager.create_selection()
    second_selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)

    with qtbot.waitSignals([first_selection.activeCatalogChanged, second_selection.activeCatalogChanged]):
        manager.apply_to_all(catalog_file.path)

    assert first_selection.active_catalog_path() == catalog_file.path
    assert second_selection.active_catalog_path() == catalog_file.path
    assert manager.default_catalog_path() == manager.built_in_catalog_path()
    assert manager.create_selection().active_catalog_path() == manager.built_in_catalog_path()


@pytest.mark.parametrize("failure", ["deleted", "invalid"])
def test_catalog_manager_failed_set_default_keeps_previous_default(
    tmp_path: Path, failure: str, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    previous_default = manager.create_catalog("Previous", base_catalog_path=small_catalog_path).path
    manager.set_default_catalog(previous_default)
    catalog_path = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path).path
    if failure == "deleted":
        catalog_path.unlink()
    else:
        document = json.loads(catalog_path.read_text(encoding="utf-8"))
        document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
        catalog_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    with pytest.raises((OSError, ValueError)):
        manager.set_default_catalog(catalog_path)

    restarted_manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    assert manager.default_catalog_path() == previous_default
    assert restarted_manager.default_catalog_path() == previous_default


@pytest.mark.parametrize("failure", ["deleted", "invalid"])
def test_catalog_manager_failed_apply_to_all_leaves_selections_unchanged(
    tmp_path: Path, failure: str, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    first_selection = manager.create_selection()
    second_selection = manager.create_selection()
    catalog_path = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path).path
    first_selection.select_catalog(catalog_path)
    if failure == "deleted":
        catalog_path.unlink()
    else:
        catalog_path.write_text("not valid catalog data\n", encoding="utf-8")
    applied: list[object] = []
    manager.catalogAppliedToAll.connect(applied.append)

    with pytest.raises((OSError, ValueError)):
        manager.apply_to_all(catalog_path)

    assert applied == []
    assert second_selection.active_catalog_path() == manager.built_in_catalog_path()
    assert second_selection.active_catalog() is manager.load_catalog(manager.built_in_catalog_path())


def test_catalog_manager_selects_and_signals_changed_catalog(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)

    with qtbot.waitSignal(selection.activeCatalogChanged) as blocker:
        selection.select_catalog(catalog_file.path)

    assert blocker.args[0] is selection.active_catalog()
    assert selection.active_catalog_path() == catalog_file.path
    assert selection.status() == "Selected catalog: user.vehicle-review"


def test_catalog_manager_reloads_active_catalog_for_changed_content(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    active_path = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path).path
    selection.select_catalog(active_path)
    before = selection.active_catalog()
    assert before is not None
    document = json.loads(active_path.read_text(encoding="utf-8"))
    document["metadata"]["name"] = "Vehicle Review Edited"
    active_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    with qtbot.waitSignal(selection.activeCatalogChanged):
        selection.reload_active_catalog()

    after = selection.active_catalog()
    assert after is not None
    assert after.content_hash != before.content_hash
    assert selection.status() == "Render catalog reloaded."


@pytest.mark.parametrize("failure", ["json", "compile"])
def test_catalog_selection_failed_selection_notifies_and_keeps_last_good_catalog(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, failure: str
) -> None:
    """Both parse and compile failures notify the UI and retain the last usable catalog."""
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    built_in_catalog = selection.active_catalog()
    broken_path = tmp_path / "broken.json"
    if failure == "json":
        broken_path.write_text("{ broken", encoding="utf-8")
    else:
        document = json.loads(small_catalog_path.read_text(encoding="utf-8"))
        document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
        broken_path.write_text(json.dumps(document), encoding="utf-8")

    with qtbot.waitSignal(selection.selectionChanged):
        selection.select_catalog(broken_path)

    assert selection.active_catalog_path() == broken_path
    assert selection.active_catalog() is built_in_catalog
    assert not selection.active_catalog_loaded()
    assert "Render catalog error" in selection.status()


def test_catalog_selection_reload_adopts_repaired_catalog_matching_last_good_catalog(
    qtbot: QtBot, tmp_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    copy_path = tmp_path / "copy.json"
    copy_path.write_text("{ broken", encoding="utf-8")
    selection.select_catalog(copy_path)
    copy_path.write_text(manager.built_in_catalog_path().read_text(encoding="utf-8"), encoding="utf-8")

    with qtbot.waitSignal(selection.activeCatalogChanged):
        selection.reload_active_catalog()

    assert selection.active_catalog_path() == copy_path
    assert selection.active_catalog_loaded()
    assert selection.status() == "Render catalog reloaded."


def test_catalog_selection_failed_reload_stays_failed_until_a_reload_compiles(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    catalog_path = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path).path
    selection.select_catalog(catalog_path)
    loaded_catalog = selection.active_catalog()
    good_text = catalog_path.read_text(encoding="utf-8")
    document = json.loads(good_text)
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    catalog_path.write_text(json.dumps(document), encoding="utf-8")

    selection.reload_active_catalog()
    manager.refresh_catalogs()

    assert selection.active_catalog() is loaded_catalog
    assert not selection.active_catalog_loaded()
    assert selection.status() == "Render catalog error: $: Unknown template: missing."

    catalog_path.write_text(good_text, encoding="utf-8")
    with qtbot.waitSignal(selection.activeCatalogChanged):
        selection.reload_active_catalog()

    assert selection.active_catalog_loaded()
    assert selection.status() == "Render catalog reloaded."


def test_catalog_selection_listing_refresh_failure_is_not_a_load_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()

    def fail_refresh() -> None:
        raise OSError("Catalog directory unreadable")

    monkeypatch.setattr(manager, "refresh_catalogs", fail_refresh)
    selection.reload_active_catalog()

    assert selection.status() == "Could not refresh render catalogs: Catalog directory unreadable"
    assert selection.active_catalog_loaded()


def test_catalog_selection_reload_refreshes_listing(tmp_path: Path, small_catalog_path: Path) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    catalog_path = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path).path
    selection.select_catalog(catalog_path)
    good_text = catalog_path.read_text(encoding="utf-8")
    catalog_path.write_text("{ broken", encoding="utf-8")
    manager.refresh_catalogs()
    assert [error.path for error in manager.listing().errors] == [catalog_path]

    catalog_path.write_text(good_text, encoding="utf-8")
    selection.reload_active_catalog()

    assert manager.listing().errors == ()
    assert catalog_path in [catalog.path for catalog in manager.listing().catalogs]
    assert selection.active_catalog_loaded()


def test_catalog_reload_keeps_the_active_catalog_for_equivalent_reordered_calculations(
    tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    path = manager.create_catalog("Ordered values", base_catalog_path=small_catalog_path).path
    document = json.loads(path.read_text(encoding="utf-8"))
    document["templates"]["marker"] = {
        "values": {"local_x": 0.25, "result_x": {"ref": ["values", "local_x"]}},
        "steps": [
            {"primitive": "point", "fields": {"position": {"x": {"ref": ["values", "result_x"]}, "y": 0}}, "style": {}}
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    selection.select_catalog(path)
    before = selection.active_catalog()
    assert before is not None

    values = document["templates"]["marker"]["values"]
    document["templates"]["marker"]["values"] = dict(reversed(list(values.items())))
    path.write_text(json.dumps(document), encoding="utf-8")
    selection.reload_active_catalog()

    assert selection.active_catalog() is before
    assert selection.active_catalog_loaded()


@pytest.mark.parametrize("problem", ["missing", "invalid"])
def test_catalog_manager_reports_active_catalog_problem_after_refresh(
    tmp_path: Path, small_catalog_path: Path, problem: str
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    selection.select_catalog(catalog_file.path)

    if problem == "missing":
        catalog_file.path.unlink()
    else:
        catalog_file.path.write_text("not valid catalog data\n", encoding="utf-8")
    manager.refresh_catalogs()

    assert selection.active_catalog_path() == catalog_file.path
    assert f"Active render catalog is {problem}" in selection.status()


def test_catalog_manager_emits_when_selected_path_changes_with_same_catalog_identity(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    duplicate_path = tmp_path / "built-in-copy.json"
    built_in_path = selection.active_catalog_path()
    duplicate_path.write_text(built_in_path.read_text(encoding="utf-8"), encoding="utf-8")
    manager.refresh_catalogs()

    with qtbot.waitSignal(selection.activeCatalogChanged):
        selection.select_catalog(duplicate_path)

    assert selection.active_catalog_path() == duplicate_path


def test_catalog_manager_supports_independent_active_selections(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    first_selection = manager.create_selection()
    second_selection = manager.create_selection()
    catalog_file = manager.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)

    with qtbot.waitSignal(first_selection.activeCatalogChanged):
        first_selection.select_catalog(catalog_file.path)

    assert first_selection.active_catalog_path() == catalog_file.path
    assert second_selection.active_catalog_path() == manager.default_catalog_path()
    assert second_selection.active_catalog() is manager.load_catalog(manager.default_catalog_path())


def _set_marker_x(path: Path, value: float) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document["templates"]["marker"]["parameters"]["x"]["default"] = value
    path.write_text(json.dumps(document), encoding="utf-8")


def test_editing_a_catalog_file_updates_the_selections_showing_it(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    catalog_file = manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    showing = manager.create_selection()
    showing.select_catalog(catalog_file.path)
    other = manager.create_selection()
    before = showing.active_catalog()

    with qtbot.waitSignal(showing.activeCatalogChanged, timeout=3000):
        _set_marker_x(catalog_file.path, 12)

    after = showing.active_catalog()
    assert before is not None and after is not None
    assert after.rendering_identity != before.rendering_identity
    assert other.active_catalog_path() == manager.built_in_catalog_path()


def test_a_catalog_file_replaced_by_an_editor_is_still_followed(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    catalog_file = manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    selection = manager.create_selection()
    selection.select_catalog(catalog_file.path)
    seen = set()

    for value in (12, 14):
        replacement = tmp_path / "replacement.tmp"
        replacement.write_text(catalog_file.path.read_text(encoding="utf-8"), encoding="utf-8")
        _set_marker_x(replacement, value)
        with qtbot.waitSignal(selection.activeCatalogChanged, timeout=3000):
            replacement.replace(catalog_file.path)
        active = selection.active_catalog()
        assert active is not None and selection.active_catalog_loaded()
        seen.add(active.rendering_identity)

    assert len(seen) == 2


def test_a_broken_catalog_file_keeps_the_last_version_until_it_is_fixed(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    catalog_file = manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    selection = manager.create_selection()
    selection.select_catalog(catalog_file.path)
    working = selection.active_catalog()
    original = catalog_file.path.read_text(encoding="utf-8")

    with qtbot.waitSignal(manager.catalogFileChanged, timeout=3000):
        catalog_file.path.write_text("{ not json", encoding="utf-8")
    qtbot.waitUntil(lambda: not selection.active_catalog_loaded())
    assert selection.active_catalog() is working
    assert "error" in selection.status().lower() or "invalid" in selection.status().lower()

    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=3000):
        catalog_file.path.write_text(original.replace('"default": 0.25', '"default": 0.5', 1), encoding="utf-8")
    assert selection.active_catalog_loaded()


def test_a_broken_default_is_still_the_chosen_default(tmp_path: Path, small_catalog_path: Path) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    catalog = manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    manager.set_default_catalog(catalog.path)

    catalog.path.write_text("{ not json", encoding="utf-8")
    listing = manager.refresh_catalogs()

    assert listing.default_path == manager.built_in_catalog_path()
    assert listing.chosen_default_path == catalog.path


def test_catalog_actions_follow_whether_a_catalog_loads_and_is_the_chosen_default(
    tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    built_in = manager.built_in_catalog_path()
    catalog = manager.create_catalog("Review", base_catalog_path=small_catalog_path)

    assert manager.can_apply_to_all(built_in) and not manager.can_use_as_default(built_in)
    assert manager.can_apply_to_all(catalog.path) and manager.can_use_as_default(catalog.path)

    manager.set_default_catalog(catalog.path)
    catalog.path.write_text("{ not json", encoding="utf-8")
    manager.refresh_catalogs()

    # The broken chosen default can be neither applied nor chosen; the built-in catalog standing in for it can.
    assert not manager.can_apply_to_all(catalog.path) and not manager.can_use_as_default(catalog.path)
    assert manager.can_use_as_default(built_in)
