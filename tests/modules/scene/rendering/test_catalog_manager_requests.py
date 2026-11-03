"""Catalog manager requests: sharing, cancelling, deleting and copying while workers are reading."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import NEVER, Cancelled, CancelToken
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogFile,
    SceneRenderCatalogListing,
    SceneRenderCatalogLoader,
    SceneRenderCatalogRevision,
    SceneRenderCatalogStore,
)
from ax_devil.modules.scene.rendering.catalog_loader import CatalogJsonDocument
from tests.catalog_helpers import GatedCatalogLoader, create_catalog, create_ready_catalog_manager, wait_for_catalogs
from tests.catalog_helpers import gated_manager as _gated_manager
from tests.catalog_helpers import loaded_revision as _revision
from tests.catalog_helpers import new_selection as _selection
from tests.catalog_helpers import ready_manager as _ready
from tests.catalog_helpers import user_catalog as _user_catalog


def _ignore(_result: object) -> None:
    """Take an outcome nobody looks at."""


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
    released: list[bool] = []

    def gated_create(name: str, base_catalog_path: Path | None = None) -> SceneRenderCatalogFile:
        started.set()
        released.append(release.wait(5))
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
    assert released == [True, True]
    assert detached == []
    assert failures == []


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
    active = selection.active_catalog()
    assert active is not None
    assert selection.status() == f"Selected catalog: {active.catalog_id}"
