"""Catalog files edited, replaced or broken on disk are followed by the selections showing them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogFile,
    SceneRenderCatalogListing,
    SceneRenderCatalogStore,
)
from ax_devil.modules.settings.paths import DEFAULT_RENDER_CATALOG_CHOICE_FILENAME
from tests.catalog_helpers import create_catalog, create_ready_catalog_manager, wait_for_catalogs
from tests.catalog_helpers import new_selection as _selection
from tests.catalog_helpers import ready_manager as _ready
from tests.catalog_helpers import refreshed_listing as _refreshed
from tests.catalog_helpers import select_catalog as _select
from tests.helpers.editor_replace import replace_like_an_editor


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
            replace_like_an_editor(replacement, catalog_file.path)
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
    assert selection.status().startswith(("Render catalog error:", "Active render catalog is invalid:"))

    edited = original.replace('"default": 0.25', '"default": 0.5', 1)
    assert edited != original
    with qtbot.waitSignal(selection.activeCatalogChanged, timeout=10_000):
        catalog_file.path.write_text(edited, encoding="utf-8")
    assert selection.active_catalog_loaded()
    fixed = selection.active_catalog()
    assert fixed is not working
    assert fixed is not None and working is not None and fixed.rendering_identity != working.rendering_identity


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


def test_a_catalog_created_while_the_first_listing_runs_is_still_discovered(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    list_catalogs = store.list_catalogs_with_errors
    late: list[SceneRenderCatalogFile] = []

    def list_then_create(token: CancelToken) -> SceneRenderCatalogListing:
        listing = list_catalogs(token)
        if not late:
            late.append(store.create_catalog("Late", base_catalog_path=small_catalog_path))
        return listing

    monkeypatch.setattr(store, "list_catalogs_with_errors", list_then_create)

    manager = create_ready_catalog_manager(qtbot, store)

    qtbot.waitUntil(lambda: late[0].path in [catalog.path for catalog in manager.listing().catalogs], timeout=10_000)
