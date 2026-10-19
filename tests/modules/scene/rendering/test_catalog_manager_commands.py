"""Apply to all and use as default: newest command wins and failures change nothing."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogStore,
)
from tests.catalog_helpers import GatedCatalogLoader, create_catalog, create_ready_catalog_manager, wait_for_catalogs
from tests.catalog_helpers import break_compilation as _break_compilation
from tests.catalog_helpers import gated_manager as _gated_manager
from tests.catalog_helpers import loaded_revision as _revision
from tests.catalog_helpers import new_selection as _selection
from tests.catalog_helpers import ready_manager as _ready
from tests.catalog_helpers import select_catalog as _select
from tests.catalog_helpers import user_catalog as _user_catalog


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
