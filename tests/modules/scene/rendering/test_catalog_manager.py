"""Catalog manager: listing, shared revisions and the default catalog."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

import ax_devil.modules.scene.rendering.catalog_manager as catalog_manager_module
from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogListing,
    SceneRenderCatalogLoader,
    SceneRenderCatalogStore,
    create_scene_render_catalog_manager,
)
from ax_devil.modules.scene.rendering.catalog_loader import CatalogJsonDocument
from tests.catalog_helpers import break_compilation as _break_compilation
from tests.catalog_helpers import create_catalog, create_ready_catalog_manager, wait_for_catalogs
from tests.catalog_helpers import loaded_revision as _revision
from tests.catalog_helpers import new_selection as _selection
from tests.catalog_helpers import ready_manager as _ready
from tests.catalog_helpers import refreshed_listing as _refreshed
from tests.catalog_helpers import reload_catalog as _reload
from tests.catalog_helpers import select_catalog as _select


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
