from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

import ax_devil.modules.scene.rendering.catalog_manager as catalog_manager_module
from ax_devil.core.cancellation import NEVER, Cancelled, CancelToken
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogFile,
    SceneRenderCatalogListing,
    SceneRenderCatalogLoader,
    SceneRenderCatalogManager,
    SceneRenderCatalogRevision,
    SceneRenderCatalogSelection,
    SceneRenderCatalogStore,
    create_scene_render_catalog_manager,
)
from ax_devil.modules.scene.rendering.catalog import CatalogJsonDocument
from ax_devil.modules.settings.paths import DEFAULT_RENDER_CATALOG_CHOICE_FILENAME
from tests.catalog_helpers import GatedCatalogLoader, create_catalog, create_ready_catalog_manager, wait_for_catalogs


def _ready(qtbot: QtBot, tmp_path: Path) -> SceneRenderCatalogManager:
    return create_ready_catalog_manager(qtbot, SceneRenderCatalogStore(tmp_path))


def _selection(qtbot: QtBot, manager: SceneRenderCatalogManager) -> SceneRenderCatalogSelection:
    selection = manager.create_selection()
    wait_for_catalogs(qtbot, manager)
    return selection


def _select(qtbot: QtBot, selection: SceneRenderCatalogSelection, path: Path) -> None:
    selection.select_catalog(path)
    wait_for_catalogs(qtbot, selection.manager)


def _reload(qtbot: QtBot, selection: SceneRenderCatalogSelection) -> None:
    selection.reload_active_catalog()
    wait_for_catalogs(qtbot, selection.manager)


def _refreshed(qtbot: QtBot, manager: SceneRenderCatalogManager) -> SceneRenderCatalogListing:
    listings: list[SceneRenderCatalogListing] = []
    manager.refresh_catalogs(on_done=listings.append)
    qtbot.waitUntil(lambda: bool(listings), timeout=10_000)
    wait_for_catalogs(qtbot, manager)
    return listings[0]


def _revision(
    qtbot: QtBot, manager: SceneRenderCatalogManager, path: Path, *, force_reload: bool = False
) -> SceneRenderCatalogRevision:
    loaded: list[SceneRenderCatalogRevision] = []
    failed: list[Exception] = []
    manager.request_revision(path, force_reload=force_reload, on_loaded=loaded.append, on_failed=failed.append)
    qtbot.waitUntil(lambda: bool(loaded or failed), timeout=10_000)
    if failed:
        raise failed[0]
    return loaded[0]


def _break_compilation(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")


def _save_after_first_read(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Make the first read of *path* be followed at once by a save that renames the catalog."""
    read_document = SceneRenderCatalogLoader.read_document
    saved = False

    def read_then_save(catalog_path: Path) -> CatalogJsonDocument:
        nonlocal saved
        document = read_document(catalog_path)
        if catalog_path == path and not saved:
            saved = True
            changed = json.loads(catalog_path.read_text(encoding="utf-8"))
            changed["metadata"]["name"] = "Saved meanwhile"
            catalog_path.write_text(json.dumps(changed), encoding="utf-8")
        return document

    monkeypatch.setattr(SceneRenderCatalogLoader, "read_document", staticmethod(read_then_save))


def test_catalog_manager_starts_selections_on_built_in_catalog(qtbot: QtBot, tmp_path: Path) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    selection = manager.create_selection()
    wait_for_catalogs(qtbot, manager)

    assert selection.active_catalog() is not None
    assert selection.active_catalog_path() == manager.built_in_catalog_path()
    assert selection.active_catalog_loaded()
    assert manager.default_catalog_path() == manager.built_in_catalog_path()
    assert sorted(tmp_path.iterdir()) == []
    assert selection.status() == "Selected catalog: built-in.standard"


def test_catalog_revision_shares_selection_cache_and_reloads_document_with_compilation(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    path = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path).path
    selection = _selection(qtbot, manager)
    _select(qtbot, selection, path)
    first = _revision(qtbot, manager, path)

    assert first.catalog is selection.active_catalog()
    assert _revision(qtbot, manager, path) is first
    document = json.loads(path.read_text(encoding="utf-8"))
    document["metadata"]["name"] = "Updated review"
    path.write_text(json.dumps(document), encoding="utf-8")
    updated = _revision(qtbot, manager, path, force_reload=True)

    assert first.document["metadata"]["name"] == "Review"
    assert updated.document["metadata"]["name"] == "Updated review"
    assert updated.catalog.content_hash == SceneRenderCatalogLoader().validate_document(updated.document).content_hash
    assert _revision(qtbot, manager, path) is updated


def test_catalog_revision_compiles_the_document_it_read_when_the_file_changes_meanwhile(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save landing between reading and compiling must not pair one version's preview with another's drawing."""
    manager = _ready(qtbot, tmp_path)
    path = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path).path
    _save_after_first_read(monkeypatch, path)

    revision = _revision(qtbot, manager, path, force_reload=True)

    assert revision.document["metadata"]["name"] == "Review"
    assert revision.catalog.content_hash == SceneRenderCatalogLoader().validate_document(revision.document).content_hash


def test_a_catalog_saved_after_it_was_read_is_read_again_by_the_next_look_at_the_files(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _ready(qtbot, tmp_path)
    path = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path).path
    _save_after_first_read(monkeypatch, path)
    assert _revision(qtbot, manager, path).document["metadata"]["name"] == "Review"

    _refreshed(qtbot, manager)

    assert _revision(qtbot, manager, path).document["metadata"]["name"] == "Saved meanwhile"


def test_requests_for_the_same_catalog_share_one_read_and_a_cancelled_request_hears_nothing(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _ready(qtbot, tmp_path)
    path = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path).path
    read_document = SceneRenderCatalogLoader.read_document
    reads: list[Path] = []

    def counting_read(catalog_path: Path) -> CatalogJsonDocument:
        if catalog_path.parent == tmp_path:  # A manager of an earlier test may still be reading its own files.
            reads.append(catalog_path)
        return read_document(catalog_path)

    monkeypatch.setattr(SceneRenderCatalogLoader, "read_document", staticmethod(counting_read))
    kept: list[SceneRenderCatalogRevision] = []
    withdrawn: list[SceneRenderCatalogRevision] = []
    failed: list[Exception] = []
    manager.request_revision(path, on_loaded=kept.append, on_failed=failed.append)
    cancel = manager.request_revision(path, on_loaded=withdrawn.append, on_failed=failed.append)
    cancel()

    qtbot.waitUntil(lambda: bool(kept), timeout=10_000)
    wait_for_catalogs(qtbot, manager)

    assert reads == [path]
    assert withdrawn == [] and failed == []


def test_a_newer_selection_replaces_one_still_loading(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    manager = _ready(qtbot, tmp_path)
    first = create_catalog(qtbot, manager, "First", base_catalog_path=small_catalog_path).path
    second = create_catalog(qtbot, manager, "Second", base_catalog_path=small_catalog_path).path
    selection = _selection(qtbot, manager)
    adopted: list[Path] = []
    selection.activeCatalogChanged.connect(lambda _catalog: adopted.append(selection.active_catalog_path()))

    selection.select_catalog(first)
    selection.select_catalog(second)
    wait_for_catalogs(qtbot, manager)

    assert adopted == [second]
    assert selection.active_catalog_path() == second


def test_catalog_files_are_read_off_the_gui_thread(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    path = store.create_catalog("Review", base_catalog_path=small_catalog_path).path
    gui_thread = threading.current_thread()
    read_text, open_path = Path.read_text, Path.open
    gui_reads: list[Path] = []
    worker_reads: list[Path] = []

    def note(file: Path) -> None:
        if file.suffix == ".json":
            (gui_reads if threading.current_thread() is gui_thread else worker_reads).append(file)

    def noting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        note(self)
        return read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    def noting_open(self: Path, *args: object, **kwargs: object) -> object:
        note(self)
        return open_path(self, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "read_text", noting_read_text)
    monkeypatch.setattr(Path, "open", noting_open)

    manager = create_ready_catalog_manager(qtbot, store)
    selection = _selection(qtbot, manager)
    _select(qtbot, selection, path)
    _reload(qtbot, selection)
    _refreshed(qtbot, manager)
    failures: list[Exception] = []
    manager.apply_to_all(path, on_failed=failures.append)
    wait_for_catalogs(qtbot, manager)
    manager.can_apply_to_all(path)
    manager.can_use_as_default(path)

    assert failures == []
    assert path in worker_reads
    assert gui_reads == []


def test_catalog_manager_starts_new_selections_on_default_catalog(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    existing_selection = _selection(qtbot, manager)
    catalog_file = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path)

    manager.set_default_catalog(catalog_file.path)
    wait_for_catalogs(qtbot, manager)
    new_selection = _selection(qtbot, manager)
    restarted_manager = _ready(qtbot, tmp_path)

    assert existing_selection.active_catalog_path() == manager.built_in_catalog_path()
    assert new_selection.active_catalog_path() == catalog_file.path
    assert _selection(qtbot, restarted_manager).active_catalog_path() == catalog_file.path


def test_a_new_default_is_in_effect_before_the_listing_refresh_returns(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    path = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path).path
    wait_for_catalogs(qtbot, manager)

    manager.set_default_catalog(path)
    selection = manager.create_selection()
    wait_for_catalogs(qtbot, manager)

    assert selection.active_catalog_path() == path


def test_catalog_manager_falls_back_to_built_in_when_default_fails_to_compile(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    broken_path = create_catalog(qtbot, manager, "Broken", base_catalog_path=small_catalog_path).path
    manager.set_default_catalog(broken_path)
    wait_for_catalogs(qtbot, manager)
    _break_compilation(broken_path)

    selection = _selection(qtbot, manager)

    assert selection.active_catalog_path() == manager.built_in_catalog_path()
    assert selection.active_catalog() is not None
    assert selection.status().startswith(
        "Using the default built-in catalog because the chosen default could not be used."
    )


def test_catalog_manager_applies_catalog_to_all_selections(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    first_selection = _selection(qtbot, manager)
    second_selection = _selection(qtbot, manager)
    catalog_file = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path)
    failures: list[Exception] = []

    with qtbot.waitSignals([first_selection.activeCatalogChanged, second_selection.activeCatalogChanged]):
        manager.apply_to_all(catalog_file.path, on_failed=failures.append)

    assert failures == []
    assert first_selection.active_catalog_path() == catalog_file.path
    assert second_selection.active_catalog_path() == catalog_file.path
    assert manager.default_catalog_path() == manager.built_in_catalog_path()
    assert _selection(qtbot, manager).active_catalog_path() == manager.built_in_catalog_path()


@pytest.mark.parametrize("failure", ["deleted", "invalid"])
def test_catalog_manager_failed_use_as_default_keeps_previous_default(
    qtbot: QtBot, tmp_path: Path, failure: str, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    previous_default = create_catalog(qtbot, manager, "Previous", base_catalog_path=small_catalog_path).path
    manager.set_default_catalog(previous_default)
    catalog_path = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path).path
    selection = _selection(qtbot, manager)
    _select(qtbot, selection, catalog_path)
    if failure == "deleted":
        catalog_path.unlink()
    else:
        _break_compilation(catalog_path)

    selection.use_as_default()
    wait_for_catalogs(qtbot, manager)

    assert manager.default_catalog_path() == previous_default
    assert _ready(qtbot, tmp_path).default_catalog_path() == previous_default


@pytest.mark.parametrize("failure", ["deleted", "invalid"])
def test_catalog_manager_failed_apply_to_all_leaves_selections_unchanged(
    qtbot: QtBot, tmp_path: Path, failure: str, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    first_selection = _selection(qtbot, manager)
    second_selection = _selection(qtbot, manager)
    catalog_path = create_catalog(qtbot, manager, "Vehicle Review", base_catalog_path=small_catalog_path).path
    _select(qtbot, first_selection, catalog_path)
    if failure == "deleted":
        catalog_path.unlink()
    else:
        catalog_path.write_text("not valid catalog data\n", encoding="utf-8")
    applied: list[object] = []
    failures: list[Exception] = []
    manager.catalogAppliedToAll.connect(applied.append)

    manager.apply_to_all(catalog_path, on_failed=failures.append)
    qtbot.waitUntil(lambda: bool(failures), timeout=10_000)

    assert applied == []
    assert second_selection.active_catalog_path() == manager.built_in_catalog_path()
    assert second_selection.active_catalog() is _revision(qtbot, manager, manager.built_in_catalog_path()).catalog


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
    copy_path = tmp_path / "copy.json"
    copy_path.write_text("{ broken", encoding="utf-8")
    _select(qtbot, selection, copy_path)
    copy_path.write_text(manager.built_in_catalog_path().read_text(encoding="utf-8"), encoding="utf-8")

    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
        selection.reload_active_catalog()

    assert selection.active_catalog_path() == copy_path
    assert selection.active_catalog_loaded()
    assert selection.status() == "Render catalog reloaded."


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


def test_a_failed_first_listing_is_retried_and_selections_on_the_stand_in_adopt_the_real_default(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    chosen = store.create_catalog("Chosen", base_catalog_path=small_catalog_path).path
    other = store.create_catalog("Other", base_catalog_path=small_catalog_path).path
    store.set_default_catalog(chosen)
    list_catalogs = store.list_catalogs_with_errors
    unreadable = True

    def list_when_readable(token: CancelToken) -> SceneRenderCatalogListing:
        if unreadable:
            raise OSError("Catalog directory unreadable")
        return list_catalogs(token)

    monkeypatch.setattr(store, "list_catalogs_with_errors", list_when_readable)
    monkeypatch.setattr(catalog_manager_module, "LISTING_RETRY_DELAY_MS", 20)
    manager = create_scene_render_catalog_manager(catalog_store=store)
    on_stand_in = manager.create_selection()
    wait_for_catalogs(qtbot, manager)
    moved = manager.create_selection()
    moved.select_catalog(other)
    wait_for_catalogs(qtbot, manager)

    assert not manager.listing_discovered
    assert on_stand_in.active_catalog_path() == manager.built_in_catalog_path()

    unreadable = False
    qtbot.waitUntil(lambda: on_stand_in.active_catalog_path() == chosen, timeout=10_000)
    wait_for_catalogs(qtbot, manager)

    assert manager.listing_discovered
    assert on_stand_in.active_catalog_loaded()
    assert moved.active_catalog_path() == other


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


def _set_marker_x(path: Path, value: float) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document["templates"]["marker"]["parameters"]["x"]["default"] = value
    path.write_text(json.dumps(document), encoding="utf-8")


def test_editing_a_catalog_file_updates_the_selections_showing_it(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    catalog_file = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    showing = _selection(qtbot, manager)
    _select(qtbot, showing, catalog_file.path)
    other = _selection(qtbot, manager)
    before = showing.active_catalog()

    with qtbot.waitSignal(showing.activeCatalogChanged, timeout=10_000):
        _set_marker_x(catalog_file.path, 12)

    after = showing.active_catalog()
    assert before is not None and after is not None
    assert after.rendering_identity != before.rendering_identity
    assert other.active_catalog_path() == manager.built_in_catalog_path()


def test_a_catalog_file_replaced_by_an_editor_is_still_followed(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    catalog_file = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    selection = _selection(qtbot, manager)
    _select(qtbot, selection, catalog_file.path)
    seen = set()

    for value in (12, 14):
        replacement = tmp_path / "replacement.tmp"
        replacement.write_text(catalog_file.path.read_text(encoding="utf-8"), encoding="utf-8")
        _set_marker_x(replacement, value)
        with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
            replacement.replace(catalog_file.path)
        wait_for_catalogs(qtbot, manager)
        active = selection.active_catalog()
        assert active is not None and selection.active_catalog_loaded()
        seen.add(active.rendering_identity)

    assert len(seen) == 2


def test_a_broken_catalog_file_keeps_the_last_version_until_it_is_fixed(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    catalog_file = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    selection = _selection(qtbot, manager)
    _select(qtbot, selection, catalog_file.path)
    working = selection.active_catalog()
    original = catalog_file.path.read_text(encoding="utf-8")

    with qtbot.waitSignal(manager.catalogFileChanged, timeout=10_000):
        catalog_file.path.write_text("{ not json", encoding="utf-8")
    qtbot.waitUntil(lambda: not selection.active_catalog_loaded(), timeout=10_000)
    assert selection.active_catalog() is working
    assert "error" in selection.status().lower() or "invalid" in selection.status().lower()

    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
        catalog_file.path.write_text(original.replace('"default": 0.25', '"default": 0.5', 1), encoding="utf-8")
    assert selection.active_catalog_loaded()


def test_a_broken_default_is_still_the_chosen_default(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    manager = _ready(qtbot, tmp_path)
    catalog = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    manager.set_default_catalog(catalog.path)
    wait_for_catalogs(qtbot, manager)

    catalog.path.write_text("{ not json", encoding="utf-8")
    listing = _refreshed(qtbot, manager)

    assert listing.default_path == manager.built_in_catalog_path()
    assert listing.chosen_default_path == catalog.path


def test_a_missing_chosen_default_that_appears_later_is_reported_as_changed(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    ghost = tmp_path / "ghost.json"
    (tmp_path / DEFAULT_RENDER_CATALOG_CHOICE_FILENAME).write_text(ghost.name, encoding="utf-8")
    manager = _ready(qtbot, tmp_path)
    assert manager.listing().chosen_default_path == ghost

    with qtbot.waitSignal(manager.catalogFileChanged, timeout=10_000) as changed:
        ghost.write_text(small_catalog_path.read_text(encoding="utf-8"), encoding="utf-8")

    assert changed.args == [ghost]


def test_catalog_actions_follow_whether_a_catalog_loads_and_is_the_chosen_default(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    built_in = manager.built_in_catalog_path()
    catalog = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    _revision(qtbot, manager, catalog.path)
    wait_for_catalogs(qtbot, manager)

    assert manager.can_apply_to_all(built_in) and not manager.can_use_as_default(built_in)
    assert manager.can_apply_to_all(catalog.path) and manager.can_use_as_default(catalog.path)

    manager.set_default_catalog(catalog.path)
    catalog.path.write_text("{ not json", encoding="utf-8")
    _refreshed(qtbot, manager)

    # The broken chosen default can be neither applied nor chosen; the built-in catalog standing in for it can.
    assert not manager.can_apply_to_all(catalog.path) and not manager.can_use_as_default(catalog.path)
    assert manager.can_use_as_default(built_in)


def test_a_catalog_not_yet_loaded_is_not_offered_for_apply_to_all(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    manager = _ready(qtbot, tmp_path)
    catalog = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    wait_for_catalogs(qtbot, manager)

    assert not manager.can_apply_to_all(catalog.path)

    _revision(qtbot, manager, catalog.path)

    assert manager.can_apply_to_all(catalog.path)


@pytest.fixture
def gated_loader() -> Iterator[GatedCatalogLoader]:
    """A loader whose held reads are released after the test, so no worker outlives it."""
    loader = GatedCatalogLoader()
    yield loader
    loader.release_all()


def _gated_manager(qtbot: QtBot, tmp_path: Path, loader: GatedCatalogLoader) -> SceneRenderCatalogManager:
    """Return a ready manager with one worker, so a held read blocks every read requested after it."""
    store = SceneRenderCatalogStore(tmp_path)
    return create_ready_catalog_manager(qtbot, store, catalog_loader=loader, jobs=BackgroundJobs(max_workers=1))


def _user_catalog(qtbot: QtBot, manager: SceneRenderCatalogManager, name: str, base: Path) -> Path:
    path = create_catalog(qtbot, manager, name, base_catalog_path=base).path
    wait_for_catalogs(qtbot, manager)
    return path


def _ignore(_result: object) -> None:
    """Take an outcome nobody looks at."""


def test_a_catalog_chosen_while_it_loads_stays_chosen_when_the_previous_one_changes(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, gated_loader: GatedCatalogLoader
) -> None:
    manager = _gated_manager(qtbot, tmp_path, gated_loader)
    chosen = _user_catalog(qtbot, manager, "Chosen", small_catalog_path)
    selection = _selection(qtbot, manager)
    previous = selection.active_catalog_path()
    before = selection.active_catalog()
    release = gated_loader.hold(chosen)

    selection.select_catalog(chosen)
    manager.catalogFileChanged.emit(previous)

    assert selection.active_catalog_path() == chosen
    assert not selection.active_catalog_loaded()
    assert selection.active_catalog() is before

    release.set()
    wait_for_catalogs(qtbot, manager)

    assert selection.active_catalog_path() == chosen
    assert selection.active_catalog_loaded()
    assert selection.active_catalog() is not before


def test_cleanup_cancels_a_new_selection_started_by_a_cached_completion(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, gated_loader: GatedCatalogLoader
) -> None:
    manager = _gated_manager(qtbot, tmp_path, gated_loader)
    cached = _user_catalog(qtbot, manager, "Cached", small_catalog_path)
    pending = _user_catalog(qtbot, manager, "Pending", small_catalog_path)
    _revision(qtbot, manager, cached)
    selection = _selection(qtbot, manager)
    release = gated_loader.hold(pending)

    def choose_next(_catalog: object) -> None:
        if selection.active_catalog_path() == cached:
            selection.select_catalog(pending)

    selection.activeCatalogChanged.connect(choose_next)
    selection.select_catalog(cached)
    assert selection.active_catalog_path() == pending
    last_good = selection.active_catalog()
    selection.cleanup()
    changed: list[object] = []
    selection.activeCatalogChanged.connect(changed.append)

    # A second consumer still wants the same read; closing this selection must leave that consumer intact.
    loaded: list[SceneRenderCatalogRevision] = []
    manager.request_revision(pending, on_loaded=loaded.append, on_failed=_ignore)
    release.set()
    qtbot.waitUntil(lambda: bool(loaded), timeout=10_000)

    assert selection.active_catalog() is last_good
    assert changed == []


def test_a_completed_apply_can_start_another_without_its_old_cancel_handle_stopping_it(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, gated_loader: GatedCatalogLoader
) -> None:
    manager = _gated_manager(qtbot, tmp_path, gated_loader)
    first = _user_catalog(qtbot, manager, "First", small_catalog_path)
    second = _user_catalog(qtbot, manager, "Second", small_catalog_path)
    selection = _selection(qtbot, manager)
    release = gated_loader.hold(second)
    completed: list[Path] = []
    failures: list[Exception] = []

    def apply_second() -> None:
        completed.append(first)
        manager.apply_to_all(second, on_done=lambda: completed.append(second), on_failed=failures.append)

    cancel_first = manager.apply_to_all(first, on_done=apply_second, on_failed=failures.append)
    qtbot.waitUntil(lambda: completed == [first], timeout=10_000)
    cancel_first()
    release.set()
    qtbot.waitUntil(lambda: completed == [first, second], timeout=10_000)

    assert failures == []
    assert selection.active_catalog_path() == second
    assert selection.active_catalog_loaded()


def test_a_catalog_file_change_does_not_drop_the_users_default_command(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, gated_loader: GatedCatalogLoader
) -> None:
    manager = _gated_manager(qtbot, tmp_path, gated_loader)
    chosen = _user_catalog(qtbot, manager, "Chosen", small_catalog_path)
    selection = _selection(qtbot, manager)
    _select(qtbot, selection, chosen)
    release = gated_loader.hold(chosen)

    selection.use_as_default()
    manager.catalogFileChanged.emit(chosen)
    release.set()

    qtbot.waitUntil(lambda: manager.default_catalog_path() == chosen, timeout=10_000)


def test_the_newest_apply_to_all_cancels_an_older_one_still_reading(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, gated_loader: GatedCatalogLoader
) -> None:
    manager = _gated_manager(qtbot, tmp_path, gated_loader)
    first = _user_catalog(qtbot, manager, "First", small_catalog_path)
    second = _user_catalog(qtbot, manager, "Second", small_catalog_path)
    one, two = _selection(qtbot, manager), _selection(qtbot, manager)
    _select(qtbot, one, first)
    _select(qtbot, two, second)
    announced: list[Path] = []
    manager.catalogAppliedToAll.connect(announced.append)
    release_first, release_second = gated_loader.hold(first), gated_loader.hold(second)

    one.apply_to_all()
    two.apply_to_all()
    release_first.set()
    release_second.set()
    qtbot.waitUntil(lambda: one.active_catalog_path() == second, timeout=10_000)
    wait_for_catalogs(qtbot, manager)

    assert announced == [second]
    assert one.active_catalog_path() == two.active_catalog_path() == second


def test_a_catalog_chosen_after_apply_to_all_was_requested_survives_its_completion(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, gated_loader: GatedCatalogLoader
) -> None:
    manager = _gated_manager(qtbot, tmp_path, gated_loader)
    applied = _user_catalog(qtbot, manager, "Applied", small_catalog_path)
    chosen = _user_catalog(qtbot, manager, "Chosen", small_catalog_path)
    requester, chooser, untouched = (_selection(qtbot, manager) for _ in range(3))
    _select(qtbot, requester, applied)
    release = gated_loader.hold(applied)

    requester.apply_to_all()
    chooser.select_catalog(chosen)
    release.set()
    qtbot.waitUntil(lambda: untouched.active_catalog_path() == applied and chooser.active_catalog_loaded())
    wait_for_catalogs(qtbot, manager)

    assert chooser.active_catalog_path() == chosen
    assert chooser.active_catalog_loaded()
    assert untouched.active_catalog_loaded()
    assert requester.active_catalog_path() == applied


def test_the_newest_default_request_cancels_an_older_one_still_reading(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, gated_loader: GatedCatalogLoader
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    manager = create_ready_catalog_manager(
        qtbot, store, catalog_loader=gated_loader, jobs=BackgroundJobs(max_workers=2)
    )
    older = _user_catalog(qtbot, manager, "Older", small_catalog_path)
    newer = _user_catalog(qtbot, manager, "Newer", small_catalog_path)
    one, two = _selection(qtbot, manager), _selection(qtbot, manager)
    _select(qtbot, one, older)
    _select(qtbot, two, newer)
    release_older, release_newer = gated_loader.hold(older), gated_loader.hold(newer)

    one.use_as_default()
    two.use_as_default()
    release_newer.set()
    qtbot.waitUntil(lambda: manager.default_catalog_path() == newer, timeout=10_000)
    release_older.set()
    wait_for_catalogs(qtbot, manager)

    assert manager.default_catalog_path() == newer


def test_a_newer_refresh_stops_the_listing_a_worker_is_in_the_middle_of(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    class GatedStore(SceneRenderCatalogStore):
        def __init__(self, catalogs_dir: Path) -> None:
            super().__init__(catalogs_dir)
            self.started = threading.Event()
            self.release = threading.Event()
            self.ended: list[str] = []
            self.gated = False

        def list_catalogs_with_errors(self, token: CancelToken = NEVER) -> SceneRenderCatalogListing:
            if not self.gated:
                return super().list_catalogs_with_errors(token)
            self.gated = False
            self.started.set()
            self.release.wait(10)
            try:
                return super().list_catalogs_with_errors(token)
            except Cancelled:
                self.ended.append("cancelled")
                raise

    store = GatedStore(tmp_path)
    manager = create_ready_catalog_manager(qtbot, store)
    listings: list[SceneRenderCatalogListing] = []
    manager.catalogListingChanged.connect(listings.append)
    store.gated = True

    manager.refresh_catalogs()
    qtbot.waitUntil(store.started.is_set, timeout=10_000)
    manager.refresh_catalogs()
    store.release.set()
    wait_for_catalogs(qtbot, manager)

    assert store.ended == ["cancelled"]
    assert len(listings) == 1


def test_deleting_a_catalog_ends_the_requests_still_reading_it(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path
) -> None:
    class SlowToReturn(SceneRenderCatalogLoader):
        """Reads at once but hands the catalog back only when the test allows."""

        def __init__(self) -> None:
            self.read = threading.Event()
            self.go = threading.Event()
            self.go.set()

        def load_revision(self, catalog_path: Path) -> SceneRenderCatalogRevision:
            revision = super().load_revision(catalog_path)
            self.read.set()
            self.go.wait(10)
            return revision

    loader = SlowToReturn()
    manager = create_ready_catalog_manager(qtbot, SceneRenderCatalogStore(tmp_path), catalog_loader=loader)
    doomed = _user_catalog(qtbot, manager, "Doomed", small_catalog_path)
    selection = _selection(qtbot, manager)
    before = selection.active_catalog_path()
    applied: list[object] = []
    failures: list[Exception] = []
    manager.catalogAppliedToAll.connect(applied.append)
    loader.go.clear()
    loader.read.clear()

    try:
        manager.apply_to_all(doomed, on_failed=failures.append)
        qtbot.waitUntil(loader.read.is_set, timeout=10_000)
        manager.delete_catalog(doomed)
    finally:
        loader.go.set()
    wait_for_catalogs(qtbot, manager)

    assert len(failures) == 1
    assert applied == []
    assert selection.active_catalog_path() == before
    assert not manager.can_apply_to_all(doomed)


def test_cancelling_every_request_of_a_restarted_read_stops_the_replacement(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, gated_loader: GatedCatalogLoader
) -> None:
    manager = _gated_manager(qtbot, tmp_path, gated_loader)
    blocker = _user_catalog(qtbot, manager, "Blocker", small_catalog_path)
    target = _user_catalog(qtbot, manager, "Target", small_catalog_path)
    later = _user_catalog(qtbot, manager, "Later", small_catalog_path)
    release = gated_loader.hold(blocker)
    manager.request_revision(blocker, on_loaded=_ignore, on_failed=_ignore)
    cancel_first = manager.request_revision(target, on_loaded=_ignore, on_failed=_ignore)
    cancel_restart = manager.request_revision(target, force_reload=True, on_loaded=_ignore, on_failed=_ignore)

    cancel_first()
    cancel_restart()
    release.set()
    _revision(qtbot, manager, later)

    assert target not in gated_loader.reads
    assert not manager.can_apply_to_all(target)


def test_deleting_the_chosen_default_takes_effect_before_discovery_returns(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _ready(qtbot, tmp_path)
    path = _user_catalog(qtbot, manager, "Default", small_catalog_path)
    manager.set_default_catalog(path)
    wait_for_catalogs(qtbot, manager)
    monkeypatch.setattr(manager, "refresh_catalogs", lambda **_kwargs: lambda: None)

    manager.delete_catalog(path)
    selection = _selection(qtbot, manager)

    assert manager.listing().chosen_default_path == manager.built_in_catalog_path()
    assert selection.active_catalog_path() == manager.built_in_catalog_path()
    assert selection.active_catalog_loaded()
    assert "error" not in selection.status().lower()


def test_accepted_copies_finish_after_detachment_and_same_names_get_distinct_files(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    manager = create_ready_catalog_manager(qtbot, store)
    started = threading.Event()
    release = threading.Event()
    original_create = store.create_catalog
    copies: list[SceneRenderCatalogFile] = []
    detached: list[SceneRenderCatalogFile] = []
    failures: list[Exception] = []

    def gated_create(name: str, base_catalog_path: Path | None = None) -> SceneRenderCatalogFile:
        started.set()
        assert release.wait(5)
        return original_create(name, base_catalog_path)

    monkeypatch.setattr(store, "create_catalog", gated_create)
    cancel = manager.create_catalog(
        "Night", base_catalog_path=small_catalog_path, on_created=detached.append, on_failed=failures.append
    )
    try:
        qtbot.waitUntil(started.is_set)
        manager.create_catalog(
            "Night", base_catalog_path=small_catalog_path, on_created=copies.append, on_failed=failures.append
        )
        cancel()
    finally:
        release.set()
    qtbot.waitUntil(lambda: len(copies) == 1)
    wait_for_catalogs(qtbot, manager)

    created = [catalog for catalog in manager.listing().catalogs if catalog.name == "Night"]
    assert len(created) == 2
    assert len({catalog.path for catalog in created}) == 2
    assert all(catalog.path.exists() for catalog in created)
    assert detached == []
    assert failures == []
