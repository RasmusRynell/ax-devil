"""Catalog selection: what a view shows while catalogs load, fail, reload and change."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogListing,
    SceneRenderCatalogStore,
)
from tests.catalog_helpers import break_compilation as _break_compilation
from tests.catalog_helpers import create_catalog, create_ready_catalog_manager, wait_for_catalogs
from tests.catalog_helpers import loaded_revision as _revision
from tests.catalog_helpers import new_selection as _selection
from tests.catalog_helpers import ready_manager as _ready
from tests.catalog_helpers import refreshed_listing as _refreshed
from tests.catalog_helpers import reload_catalog as _reload
from tests.catalog_helpers import select_catalog as _select


def test_catalog_manager_selects_and_signals_changed_catalog(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    catalog_file = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path)

    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000) as blocker:
        selection.select_catalog(catalog_file.path)

    assert blocker.args[0] is selection.active_catalog()
    assert selection.active_catalog_path() == catalog_file.path
    assert selection.status() == "Selected catalog: user.vehicle-review"


def test_catalog_manager_reloads_active_catalog_for_changed_content(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    active_path = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path).path
    _select(qtbot, selection, active_path)
    before = selection.active_catalog()
    assert before is not None
    document = json.loads(active_path.read_text(encoding="utf-8"))
    document["metadata"]["name"] = "Vehicle Review Edited"
    active_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
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
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    built_in_catalog = selection.active_catalog()
    broken_path = tmp_path / "broken.json"
    if failure == "json":
        broken_path.write_text("{ broken", encoding="utf-8")
    else:
        broken_path.write_text(small_catalog_path.read_text(encoding="utf-8"), encoding="utf-8")
        _break_compilation(broken_path)

    with qtbot.waitSignal(selection.selectionChanged, timeout=10_000):
        selection.select_catalog(broken_path)
    wait_for_catalogs(qtbot, manager)

    assert selection.active_catalog_path() == broken_path
    assert selection.active_catalog() is built_in_catalog
    assert not selection.active_catalog_loaded()
    assert "Render catalog error" in selection.status()


def test_catalog_selection_reload_adopts_repaired_catalog_matching_last_good_catalog(
    qtbot: QtBot, tmp_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    last_good = selection.active_catalog()
    assert last_good is not None
    copy_path = tmp_path / "copy.json"
    copy_path.write_text("{ broken", encoding="utf-8")
    _select(qtbot, selection, copy_path)
    copy_path.write_text(manager.built_in_catalog_path().read_text(encoding="utf-8"), encoding="utf-8")

    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
        selection.reload_active_catalog()

    assert selection.active_catalog_path() == copy_path
    assert selection.active_catalog_loaded()
    # A file-watcher reload may win the explicit reload; both must adopt the repaired path.
    repaired = selection.active_catalog()
    assert repaired is not None
    assert repaired.content_hash == last_good.content_hash


def test_catalog_selection_failed_reload_stays_failed_until_a_reload_compiles(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    catalog_path = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path).path
    _select(qtbot, selection, catalog_path)
    loaded_catalog = selection.active_catalog()
    good_text = catalog_path.read_text(encoding="utf-8")
    _break_compilation(catalog_path)

    _reload(qtbot, selection)

    assert selection.active_catalog() is loaded_catalog
    assert not selection.active_catalog_loaded()
    assert selection.status() == "Render catalog error: $: Unknown template: missing."

    catalog_path.write_text(good_text, encoding="utf-8")
    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
        selection.reload_active_catalog()

    assert selection.active_catalog_loaded()
    assert selection.status() == "Render catalog reloaded."


def test_catalog_selection_listing_refresh_failure_is_not_a_load_failure(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    manager = create_ready_catalog_manager(qtbot, store)
    selection = _selection(qtbot, manager)

    def fail_listing(_token: CancelToken) -> SceneRenderCatalogListing:
        raise OSError("Catalog directory unreadable")

    monkeypatch.setattr(store, "list_catalogs_with_errors", fail_listing)
    _reload(qtbot, selection)

    assert selection.status() == "Could not refresh render catalogs: Catalog directory unreadable"
    assert selection.active_catalog_loaded()


def test_catalog_selection_reload_refreshes_listing(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    catalog_path = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path).path
    _select(qtbot, selection, catalog_path)
    good_text = catalog_path.read_text(encoding="utf-8")
    catalog_path.write_text("{ broken", encoding="utf-8")
    _refreshed(qtbot, manager)
    assert [error.path for error in manager.listing().errors] == [catalog_path]

    catalog_path.write_text(good_text, encoding="utf-8")
    _reload(qtbot, selection)

    assert manager.listing().errors == ()
    assert catalog_path in [catalog.path for catalog in manager.listing().catalogs]
    assert selection.active_catalog_loaded()


def test_catalog_reload_keeps_the_active_catalog_for_equivalent_reordered_calculations(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    path = create_catalog(qtbot, manager, "Ordered values", base_catalog_path=small_catalog_path).path
    document = json.loads(path.read_text(encoding="utf-8"))
    document["templates"]["marker"] = {
        "values": {"local_x": 0.25, "result_x": {"ref": ["values", "local_x"]}},
        "steps": [
            {"primitive": "point", "fields": {"position": {"x": {"ref": ["values", "result_x"]}, "y": 0}}, "style": {}}
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    _select(qtbot, selection, path)
    before = selection.active_catalog()
    assert before is not None

    values = document["templates"]["marker"]["values"]
    document["templates"]["marker"]["values"] = dict(reversed(list(values.items())))
    path.write_text(json.dumps(document), encoding="utf-8")
    _reload(qtbot, selection)

    assert selection.active_catalog() is before
    assert selection.active_catalog_loaded()


@pytest.mark.parametrize("problem", ["missing", "invalid"])
def test_catalog_manager_reports_active_catalog_problem_after_refresh(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, problem: str
) -> None:
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    catalog_file = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path)
    _select(qtbot, selection, catalog_file.path)

    if problem == "missing":
        catalog_file.path.unlink()
    else:
        catalog_file.path.write_text("not valid catalog data\n", encoding="utf-8")
    _refreshed(qtbot, manager)

    assert selection.active_catalog_path() == catalog_file.path
    assert f"Active render catalog is {problem}" in selection.status()


def test_catalog_manager_emits_when_selected_path_changes_with_same_catalog_identity(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    manager = _ready(qtbot, tmp_path)
    selection = _selection(qtbot, manager)
    duplicate_path = tmp_path / "built-in-copy.json"
    built_in_path = selection.active_catalog_path()
    duplicate_path.write_text(built_in_path.read_text(encoding="utf-8"), encoding="utf-8")
    _refreshed(qtbot, manager)

    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
        selection.select_catalog(duplicate_path)

    assert selection.active_catalog_path() == duplicate_path


def test_catalog_manager_supports_independent_active_selections(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    first_selection = _selection(qtbot, manager)
    second_selection = _selection(qtbot, manager)
    catalog_file = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path)

    with qtbot.waitSignal(first_selection.activeCatalogChanged, timeout=10_000):
        first_selection.select_catalog(catalog_file.path)

    assert first_selection.active_catalog_path() == catalog_file.path
    assert second_selection.active_catalog_path() == manager.default_catalog_path()
    assert second_selection.active_catalog() is _revision(qtbot, manager, manager.default_catalog_path()).catalog
