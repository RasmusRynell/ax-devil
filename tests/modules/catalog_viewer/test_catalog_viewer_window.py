"""Catalog-manager notifications update previews; file watching is covered by manager tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QTabBar
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.catalog_viewer import CatalogViewerWindow, close_catalog_viewer, show_catalog_viewer
from ax_devil.modules.catalog_viewer.inspector import ObjectInspector
from ax_devil.modules.catalog_viewer.sheets import sheet_frame, sheets
from ax_devil.modules.catalog_viewer.window import NewCatalogDialog
from ax_devil.modules.object_inspection.fields_view import FieldsView
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogFile,
    SceneRenderCatalogLoader,
    SceneRenderCatalogManager,
    SceneRenderCatalogStore,
)
from ax_devil.modules.scene.rendering.catalog import CatalogJsonDocument
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer
from tests.catalog_helpers import (
    GatedCatalogLoader,
    create_catalog,
    create_ready_catalog_manager,
    wait_for_catalogs,
)


def _viewer(qtbot: QtBot, manager: SceneRenderCatalogManager, path: Path) -> CatalogViewerWindow:
    window = CatalogViewerWindow(manager)
    qtbot.addWidget(window)
    window.show_catalog(path)
    window.show()
    wait_for_catalogs(qtbot, manager)
    return window


def _file_changed(qtbot: QtBot, manager: SceneRenderCatalogManager, path: Path) -> None:
    """Look at the files again, then send the notice the file watcher sends for *path*, and let the viewer settle."""
    listed: list[object] = []
    manager.refresh_catalogs(on_done=listed.append)
    qtbot.waitUntil(lambda: bool(listed), timeout=10_000)
    manager.catalogFileChanged.emit(path)
    wait_for_catalogs(qtbot, manager)


def _relabel_person(path: Path, label: str, source: str | None = None) -> None:
    document = json.loads(source if source is not None else path.read_text(encoding="utf-8"))
    person = next(recipe for recipe in document["recipes"]["classifications"] if recipe["id"] == "human")
    person["label"] = label
    path.write_text(json.dumps(document), encoding="utf-8")


def _banner(window: CatalogViewerWindow) -> QLabel:
    banner = window.findChild(QLabel, "catalogViewerError")
    assert banner is not None
    return banner


def _draws_a_sheet(window: CatalogViewerWindow) -> bool:
    renderer = window.findChild(VideoFrameRenderer, "catalogViewerRenderer")
    assert renderer is not None
    return renderer.frame_display_rect() is not None


def _sheet_description(window: CatalogViewerWindow) -> str:
    """Return the description of the selected sheet, which its tab also shows as a tooltip."""
    tabs = window.findChild(QTabBar, "catalogViewerTabs")
    assert tabs is not None
    return tabs.tabToolTip(tabs.currentIndex())


def _shows_text(window: CatalogViewerWindow, text: str) -> bool:
    return any(label.text() == text for label in window.findChildren(QLabel))


def test_the_viewer_shows_a_tab_per_sheet(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> None:
    window = _viewer(qtbot, render_catalog_manager, render_catalog_manager.built_in_catalog_path())

    titles = window.sheet_titles()

    document = json.loads(render_catalog_manager.built_in_catalog_path().read_text(encoding="utf-8"))
    assert titles == [sheet.title for sheet in sheets(document)]
    assert titles
    assert window.error() is None


def test_clicking_a_sheet_object_shows_its_generated_attributes_until_empty_video_is_clicked(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    path = render_catalog_manager.built_in_catalog_path()
    window = _viewer(qtbot, render_catalog_manager, path)
    window.resize(1400, 800)
    renderer = window.findChild(VideoFrameRenderer, "catalogViewerRenderer")
    inspector = window.findChild(ObjectInspector)
    assert renderer is not None and inspector is not None
    fields = inspector.findChild(FieldsView)
    assert fields is not None and not fields.isVisible()
    area = renderer.frame_display_rect()
    assert area is not None
    sheet = sheets(json.loads(path.read_text(encoding="utf-8")))[0]
    provider = sheet_frame(sheet, SceneRenderCatalogLoader().load_path(path)).overlays
    assert provider is not None and provider.interaction_provider is not None
    points = [((nx + 0.5) / 20, (ny + 0.5) / 20) for nx in range(20) for ny in range(20)]
    hit = next(hit for point in points if (hit := provider.interaction_provider.hit_test(*point)) is not None)
    x, y, width, height = hit.bounds
    on_object = (x + width / 2, y + height / 2)  # The middle of an object's box, away from its edges.
    off_object = next(point for point in points if provider.interaction_provider.hit_test(*point) is None)

    def click(point: tuple[float, float]) -> None:
        pos = QPoint(round(area.x() + point[0] * area.width()), round(area.y() + point[1] * area.height()))
        QTest.mouseClick(renderer, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)

    click(on_object)
    assert fields.isVisible() and fields.model().rowCount() > 0
    click(off_object)
    assert not fields.isVisible()


def test_catalog_change_notification_redraws_the_file(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    assert "Human" in window.sheet_titles()

    _relabel_person(catalog.path, "Pedestrian")

    _file_changed(qtbot, render_catalog_manager, catalog.path)
    assert "Pedestrian" in window.sheet_titles()
    assert "Human" not in window.sheet_titles()


def test_a_save_during_loading_shows_the_version_read_until_the_change_notification(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    monkeypatch: pytest.MonkeyPatch,
    small_catalog_path: Path,
) -> None:
    path = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path).path
    read_document = SceneRenderCatalogLoader.read_document
    saved = False

    def read_then_save(catalog_path: Path) -> CatalogJsonDocument:
        nonlocal saved
        document = read_document(catalog_path)
        if catalog_path == path and not saved:
            saved = True
            _relabel_person(path, "Pedestrian")
        return document

    monkeypatch.setattr(SceneRenderCatalogLoader, "read_document", staticmethod(read_then_save))

    window = _viewer(qtbot, render_catalog_manager, path)

    assert "Human" in window.sheet_titles()
    assert "Pedestrian" not in window.sheet_titles()
    _file_changed(qtbot, render_catalog_manager, path)
    assert "Pedestrian" in window.sheet_titles()
    assert "Human" not in window.sheet_titles()


def test_a_broken_file_shows_its_error_over_the_last_version_until_fixed(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    titles = window.sheet_titles()
    description = _sheet_description(window)
    assert description and _shows_text(window, description)
    original = catalog.path.read_text(encoding="utf-8")

    unsupported = json.loads(original)
    unsupported["metadata"]["schema_version"] = 99
    catalog.path.write_text(json.dumps(unsupported), encoding="utf-8")
    _file_changed(qtbot, render_catalog_manager, catalog.path)
    assert window.error() is not None

    assert "$.metadata.schema_version" in (window.error() or "")
    assert window.sheet_titles() == titles
    assert _draws_a_sheet(window)
    assert _shows_text(window, description)
    assert "Showing the last version that loaded." in _banner(window).text()

    _relabel_person(catalog.path, "Pedestrian", original)
    _file_changed(qtbot, render_catalog_manager, catalog.path)
    assert window.error() is None
    assert "Pedestrian" in window.sheet_titles()


@pytest.mark.parametrize(
    ("hidden", "missing"), [(False, False), (True, True)], ids=["visible-invalid", "hidden-missing"]
)
def test_switching_to_a_catalog_that_does_not_load_clears_the_previous_preview(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    small_catalog_path: Path,
    tmp_path: Path,
    hidden: bool,
    missing: bool,
) -> None:
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    titles = window.sheet_titles()
    description = _sheet_description(window)
    assert description and _shows_text(window, description)
    assert _draws_a_sheet(window)
    if hidden:
        window.hide()
    broken = tmp_path / "broken.json"
    if not missing:
        broken.write_text("{ not json", encoding="utf-8")

    window.show_catalog(broken)
    wait_for_catalogs(qtbot, render_catalog_manager)

    assert window.catalog_path() == broken
    assert window.error() is not None
    assert window.sheet_titles() == []
    assert "Nothing to show yet." in _banner(window).text()
    assert not _draws_a_sheet(window)
    assert not _shows_text(window, description)

    window.show_catalog(catalog.path)
    window.show()
    wait_for_catalogs(qtbot, render_catalog_manager)

    assert window.error() is None
    assert window.sheet_titles() == titles
    assert _draws_a_sheet(window)
    assert _shows_text(window, description)


def test_a_catalog_without_a_preview_starts_drawing_when_fixed(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    source = create_catalog(qtbot, render_catalog_manager, "Source", base_catalog_path=small_catalog_path)
    repaired = create_catalog(qtbot, render_catalog_manager, "Repair", base_catalog_path=small_catalog_path)
    original = repaired.path.read_text(encoding="utf-8")
    repaired.path.write_text("{ not json", encoding="utf-8")
    window = _viewer(qtbot, render_catalog_manager, source.path)

    window.show_catalog(repaired.path)
    wait_for_catalogs(qtbot, render_catalog_manager)

    assert window.error() is not None
    assert not _draws_a_sheet(window)
    _relabel_person(repaired.path, "Pedestrian", original)
    _file_changed(qtbot, render_catalog_manager, repaired.path)
    assert window.error() is None

    assert "Pedestrian" in window.sheet_titles()
    assert _draws_a_sheet(window)
    assert _shows_text(window, _sheet_description(window))
    assert _banner(window).isHidden()


def test_showing_the_viewer_again_reuses_the_window(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)
    first = show_catalog_viewer(render_catalog_manager)
    qtbot.addWidget(first)

    second = show_catalog_viewer(render_catalog_manager, catalog_path=catalog.path)

    assert second is first
    assert first.catalog_path() == catalog.path


def test_the_viewer_follows_a_new_default_catalog(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    window = _viewer(qtbot, render_catalog_manager, render_catalog_manager.default_catalog_path())
    catalog = create_catalog(qtbot, render_catalog_manager, "Review", base_catalog_path=small_catalog_path)

    render_catalog_manager.set_default_catalog(catalog.path)

    qtbot.waitUntil(lambda: window.catalog_path() == catalog.path)


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
    monkeypatch.setattr("ax_devil.modules.catalog_viewer.window.ask", lambda *_args: answers.pop(0))

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


def test_closing_the_viewer_through_its_manager_cleans_it_up(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    """Closing destroys the old viewer; reopening displays a usable catalog preview."""
    window = show_catalog_viewer(render_catalog_manager)
    qtbot.addWidget(window)
    qtbot.waitExposed(window)
    qtbot.waitUntil(lambda: not window._renderer._quick.isHidden())
    assert not window._renderer._quick.grabFramebuffer().isNull()

    close_catalog_viewer(render_catalog_manager)
    qtbot.waitUntil(lambda: not isValid(window))
    reopened = show_catalog_viewer(render_catalog_manager)
    qtbot.addWidget(reopened)
    qtbot.waitExposed(reopened)
    qtbot.waitUntil(lambda: not reopened._renderer._quick.isHidden())

    assert reopened is not window
    assert show_catalog_viewer(render_catalog_manager) is reopened
    assert reopened.error() is None
    assert not reopened._renderer._quick.grabFramebuffer().isNull()


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
    monkeypatch.setattr("ax_devil.modules.catalog_viewer.window.notify", lambda *_args: None)
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
    monkeypatch.setattr("ax_devil.modules.catalog_viewer.window.notify", lambda *_args: warnings.append(True))

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
    monkeypatch.setattr("ax_devil.modules.catalog_viewer.window.notify", lambda *_args: warnings.append(True))
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
