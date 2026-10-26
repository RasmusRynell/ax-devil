"""The catalog viewer's commands on catalog files: New copy, Delete, Apply to all and Use as default."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.catalog_viewer import CatalogViewerWindow
from ax_devil.modules.catalog_viewer.new_catalog_dialog import NewCatalogDialog
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogFile,
    SceneRenderCatalogManager,
    SceneRenderCatalogStore,
)
from tests.catalog_helpers import (
    GatedCatalogLoader,
    create_catalog,
    create_ready_catalog_manager,
    wait_for_catalogs,
)
from tests.helpers.catalog_viewer import error_banner as _banner
from tests.helpers.catalog_viewer import file_changed as _file_changed
from tests.helpers.catalog_viewer import open_viewer as _viewer
from tests.helpers.catalog_viewer import relabel_person as _relabel_person


def _button(window: CatalogViewerWindow, name: str) -> QPushButton:
    button = window.findChild(QPushButton, name)
    assert button is not None
    return button


def test_new_copy_creates_a_catalog_from_the_one_shown_and_shows_it(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    monkeypatch: pytest.MonkeyPatch,
    small_catalog_path: Path,
) -> None:
    source = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    _relabel_person(source.path, "Pedestrian")
    window = _viewer(qtbot, render_catalog_manager, source.path)
    monkeypatch.setattr(NewCatalogDialog, "exec", lambda self: self.name_edit.setText("Night") or 1)

    _button(window, "catalogViewerNew").click()

    qtbot.waitUntil(lambda: window.catalog_path().name == "night.json")
    wait_for_catalogs(qtbot, render_catalog_manager)
    assert "Pedestrian" in window.sheet_titles()
    assert source.path.exists()


def test_delete_removes_the_catalog_after_asking_and_shows_the_default(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    monkeypatch: pytest.MonkeyPatch,
    small_catalog_path: Path,
) -> None:
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    render_catalog_manager.set_default_catalog(catalog.path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    answers = [False, True]
    monkeypatch.setattr("ax_devil.modules.catalog_viewer.catalog_file_actions.ask", lambda *_args: answers.pop(0))

    _button(window, "catalogViewerDelete").click()
    assert catalog.path.exists()
    _button(window, "catalogViewerDelete").click()

    qtbot.waitUntil(lambda: window.catalog_path() == render_catalog_manager.built_in_catalog_path())
    wait_for_catalogs(qtbot, render_catalog_manager)
    assert not catalog.path.exists()
    assert not _button(window, "catalogViewerDelete").isEnabled()


def test_a_catalog_still_loading_cannot_be_copied_yet(qtbot: QtBot, tmp_path: Path, small_catalog_path: Path) -> None:
    """Copying reads the file, so it waits for the load instead of freezing the window on slow storage."""
    store = SceneRenderCatalogStore(tmp_path / "render_catalogs")
    path = store.create_catalog("Review", base_catalog_path=small_catalog_path).path
    loader = GatedCatalogLoader()
    manager = create_ready_catalog_manager(qtbot, store, catalog_loader=loader)
    release = loader.hold(path)
    window = CatalogViewerWindow(manager)
    qtbot.addWidget(window)
    try:
        window.show_catalog(path)
        window.show()
        assert not _button(window, "catalogViewerNew").isEnabled()

        release.set()
        wait_for_catalogs(qtbot, manager)

        assert _button(window, "catalogViewerNew").isEnabled()
    finally:
        release.set()


def test_a_broken_catalog_can_be_deleted_but_not_copied(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    catalog.path.write_text("{ not json", encoding="utf-8")

    window = _viewer(qtbot, render_catalog_manager, catalog.path)

    assert not _button(window, "catalogViewerNew").isEnabled()
    assert _button(window, "catalogViewerDelete").isEnabled()
    assert not _button(window, "catalogViewerApplyToAll").isEnabled()
    assert not _button(window, "catalogViewerUseAsDefault").isEnabled()


def test_the_catalog_shown_can_be_applied_to_every_view_and_made_the_default(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    selection = render_catalog_manager.create_selection()
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    assert selection.active_catalog_path() != catalog.path

    _button(window, "catalogViewerApplyToAll").click()

    qtbot.waitUntil(lambda: selection.active_catalog_path() == catalog.path)
    assert render_catalog_manager.default_catalog_path() != catalog.path

    _button(window, "catalogViewerUseAsDefault").click()

    qtbot.waitUntil(lambda: render_catalog_manager.default_catalog_path() == catalog.path)
    wait_for_catalogs(qtbot, render_catalog_manager)
    assert window.catalog_path() == catalog.path
    assert not _button(window, "catalogViewerUseAsDefault").isEnabled()


def test_a_default_catalog_that_breaks_stays_on_screen_with_its_error(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    render_catalog_manager.set_default_catalog(catalog.path)
    wait_for_catalogs(qtbot, render_catalog_manager)
    window = _viewer(qtbot, render_catalog_manager, render_catalog_manager.listing().chosen_default_path)

    catalog.path.write_text("{ not json", encoding="utf-8")
    _file_changed(qtbot, render_catalog_manager, catalog.path)
    assert window.error() is not None

    assert window.catalog_path() == catalog.path
    assert render_catalog_manager.default_catalog_path() == render_catalog_manager.built_in_catalog_path()


@pytest.mark.parametrize("action", ["catalogViewerApplyToAll", "catalogViewerUseAsDefault"])
@pytest.mark.parametrize("valid", [True, False])
def test_forced_catalog_commands_show_the_revision_they_actually_loaded(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    small_catalog_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    valid: bool,
) -> None:
    manager = render_catalog_manager
    source = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, manager, source.path)
    manager._watcher.blockSignals(True)
    monkeypatch.setattr("ax_devil.modules.catalog_viewer.catalog_file_actions.QMessageBox.warning", lambda *_args: None)
    if valid:
        _relabel_person(source.path, "Pedestrian")
    else:
        source.path.write_text("{ broken", encoding="utf-8")

    _button(window, action).click()
    wait_for_catalogs(qtbot, manager)

    if valid:
        assert "Pedestrian" in window.sheet_titles()
        assert window.error() is None
        assert _button(window, "catalogViewerNew").isEnabled()
    else:
        assert window.error() is not None
        assert "Human" in window.sheet_titles()
        assert not _button(window, "catalogViewerNew").isEnabled()
        assert _banner(window).isVisible()


def test_a_default_persistence_failure_keeps_the_loaded_revision_live(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    small_catalog_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = render_catalog_manager
    source = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, manager, source.path)
    manager._watcher.blockSignals(True)
    warnings: list[object] = []
    monkeypatch.setattr(
        "ax_devil.modules.catalog_viewer.catalog_file_actions.QMessageBox.warning", lambda *_args: warnings.append(True)
    )

    def cannot_save(_path: Path) -> None:
        raise OSError("Choice cannot be saved")

    monkeypatch.setattr(manager.catalog_store, "set_default_catalog", cannot_save)
    _relabel_person(source.path, "Pedestrian")
    _button(window, "catalogViewerUseAsDefault").click()
    wait_for_catalogs(qtbot, manager)

    assert warnings == [True]
    assert window.error() is None
    assert "Pedestrian" in window.sheet_titles()
    assert _button(window, "catalogViewerNew").isEnabled()
    assert manager.default_catalog_path() != source.path


def test_a_late_apply_failure_does_not_mark_a_different_catalog_invalid(
    qtbot: QtBot, tmp_path: Path, small_catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    first = store.create_catalog("First", small_catalog_path).path
    second = store.create_catalog("Second", small_catalog_path).path
    loader = GatedCatalogLoader()
    manager = create_ready_catalog_manager(qtbot, store, catalog_loader=loader)
    window = _viewer(qtbot, manager, first)
    manager._watcher.blockSignals(True)
    warnings: list[object] = []
    monkeypatch.setattr(
        "ax_devil.modules.catalog_viewer.catalog_file_actions.QMessageBox.warning", lambda *_args: warnings.append(True)
    )
    first.write_text("{ broken", encoding="utf-8")
    prior_reads = loader.reads.count(first)
    release = loader.hold(first)
    try:
        _button(window, "catalogViewerApplyToAll").click()
        qtbot.waitUntil(lambda: loader.reads.count(first) > prior_reads)
        window.show_catalog(second)
    finally:
        release.set()
    wait_for_catalogs(qtbot, manager)

    assert window.catalog_path() == second
    assert window.error() is None
    assert _button(window, "catalogViewerNew").isEnabled()
    assert warnings == []


def test_new_copy_keeps_the_gui_responsive_and_finishes_after_its_window_closes(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    small_catalog_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = render_catalog_manager
    source = create_catalog(qtbot, manager, "Review", base_catalog_path=small_catalog_path)
    window = CatalogViewerWindow(manager)
    window.show_catalog(source.path)
    window.show()
    wait_for_catalogs(qtbot, manager)
    monkeypatch.setattr(NewCatalogDialog, "exec", lambda self: self.name_edit.setText("Night") or 1)
    started = threading.Event()
    release = threading.Event()
    original_create = manager.catalog_store.create_catalog
    create_threads: list[threading.Thread] = []
    shown: list[Path] = []

    def gated_copy(name: str, base_catalog_path: Path | None = None) -> SceneRenderCatalogFile:
        create_threads.append(threading.current_thread())
        started.set()
        assert release.wait(5)
        return original_create(name, base_catalog_path)

    monkeypatch.setattr(manager.catalog_store, "create_catalog", gated_copy)
    try:
        _button(window, "catalogViewerNew").click()
        qtbot.waitUntil(started.is_set)
        heartbeat: list[bool] = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qtbot.waitUntil(lambda: bool(heartbeat))
        assert len(create_threads) == 1
        assert create_threads[0] is not threading.current_thread()
        monkeypatch.setattr(window, "show_catalog", shown.append)
        window.close()
        qtbot.waitUntil(lambda: not isValid(window))
    finally:
        release.set()
        if isValid(window):
            window.close()
    qtbot.waitUntil(lambda: any(catalog.name == "Night" for catalog in manager.listing().catalogs))
    wait_for_catalogs(qtbot, manager)
    assert shown == []
