"""The catalog viewer follows its catalog file: saved changes are drawn at once, and a broken file keeps the last."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.catalog_viewer import CatalogViewerWindow, close_catalog_viewer, show_catalog_viewer
from ax_devil.modules.catalog_viewer.sheets import sheets
from ax_devil.modules.catalog_viewer.window import NewCatalogDialog
from ax_devil.modules.scene.rendering import SceneRenderCatalogLoader, SceneRenderCatalogManager
from ax_devil.modules.scene.rendering.catalog import CatalogJsonDocument


def _viewer(qtbot: QtBot, manager: SceneRenderCatalogManager, path: Path) -> CatalogViewerWindow:
    window = CatalogViewerWindow(manager)
    qtbot.addWidget(window)
    window.show_catalog(path)
    window.show()
    return window


def _relabel_person(path: Path, label: str, source: str | None = None) -> None:
    document = json.loads(source if source is not None else path.read_text(encoding="utf-8"))
    person = next(recipe for recipe in document["recipes"]["classifications"] if recipe["id"] == "human")
    person["label"] = label
    path.write_text(json.dumps(document), encoding="utf-8")


def test_the_viewer_shows_a_tab_per_sheet(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> None:
    window = _viewer(qtbot, render_catalog_manager, render_catalog_manager.built_in_catalog_path())

    titles = window.sheet_titles()

    document = json.loads(render_catalog_manager.built_in_catalog_path().read_text(encoding="utf-8"))
    assert titles == [sheet.title for sheet in sheets(document)]
    assert titles
    assert window.error() is None


def test_saving_the_file_redraws_it(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    assert "Human" in window.sheet_titles()

    _relabel_person(catalog.path, "Pedestrian")

    qtbot.waitUntil(lambda: "Pedestrian" in window.sheet_titles(), timeout=3000)
    assert "Human" not in window.sheet_titles()


def test_a_save_during_loading_keeps_the_preview_and_compilation_on_one_revision(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    monkeypatch: pytest.MonkeyPatch,
    small_catalog_path: Path,
) -> None:
    path = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path).path
    read_document = SceneRenderCatalogLoader.read_document
    original = read_document(path)
    expected = SceneRenderCatalogLoader().validate_document(original)
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
    assert window._catalog is not None
    assert window._catalog.content_hash == expected.content_hash
    qtbot.waitUntil(lambda: "Pedestrian" in window.sheet_titles(), timeout=3000)
    current = SceneRenderCatalogLoader().validate_document(read_document(path))
    assert window._catalog.content_hash == current.content_hash


def test_a_broken_file_shows_its_error_over_the_last_version_until_fixed(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    titles = window.sheet_titles()
    qtbot.waitUntil(lambda: not window._renderer._quick.isHidden())
    frame = window._renderer._video_frame
    description = window._description.text()
    original = catalog.path.read_text(encoding="utf-8")

    catalog.path.write_text(original.replace('"schema_version": 3', '"schema_version": 99', 1), encoding="utf-8")
    qtbot.waitUntil(lambda: window.error() is not None, timeout=3000)

    assert "$.metadata.schema_version" in (window.error() or "")
    assert window.sheet_titles() == titles
    assert frame is not None
    assert window._renderer._video_frame is frame
    assert window._description.text() == description
    assert "Showing the last version that loaded." in window._banner.text()

    _relabel_person(catalog.path, "Pedestrian", original)
    qtbot.waitUntil(lambda: window.error() is None, timeout=3000)
    assert "Pedestrian" in window.sheet_titles()


@pytest.mark.parametrize("hidden", [False, True], ids=["visible", "hidden"])
@pytest.mark.parametrize("missing", [False, True], ids=["invalid", "missing"])
def test_switching_to_a_catalog_that_does_not_load_clears_the_previous_preview(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    small_catalog_path: Path,
    tmp_path: Path,
    hidden: bool,
    missing: bool,
) -> None:
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    titles = window.sheet_titles()
    description = window._description.text()
    assert window._renderer.frame_display_rect() is not None
    assert description
    qtbot.waitUntil(lambda: not window._renderer._quick.isHidden())
    if hidden:
        window.hide()
    broken = tmp_path / "broken.json"
    if not missing:
        broken.write_text("{ not json", encoding="utf-8")

    window.show_catalog(broken)

    assert window.catalog_path() == broken
    assert window.error() is not None
    assert window.sheet_titles() == []
    assert "Nothing to show yet." in window._banner.text()
    assert window._renderer.frame_display_rect() is None
    assert window._renderer._quick.isHidden()
    assert window._description.text() == ""
    assert window._description.styleSheet() == ""

    window.show_catalog(catalog.path)
    window.show()

    assert window.error() is None
    assert window.sheet_titles() == titles
    assert window._renderer.frame_display_rect() is not None
    assert window._description.text() == description


def test_a_catalog_without_a_preview_starts_drawing_when_fixed(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    source = render_catalog_manager.create_catalog("Source", base_catalog_path=small_catalog_path)
    repaired = render_catalog_manager.create_catalog("Repair", base_catalog_path=small_catalog_path)
    original = repaired.path.read_text(encoding="utf-8")
    repaired.path.write_text("{ not json", encoding="utf-8")
    window = _viewer(qtbot, render_catalog_manager, source.path)

    window.show_catalog(repaired.path)

    assert window.error() is not None
    assert window._renderer.frame_display_rect() is None
    _relabel_person(repaired.path, "Pedestrian", original)
    qtbot.waitUntil(lambda: window.error() is None, timeout=3000)

    assert "Pedestrian" in window.sheet_titles()
    assert window._renderer.frame_display_rect() is not None
    assert window._description.text()
    assert window._banner.isHidden()


def test_showing_the_viewer_again_reuses_the_window(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    first = show_catalog_viewer(render_catalog_manager)
    qtbot.addWidget(first)

    second = show_catalog_viewer(render_catalog_manager, catalog_path=catalog.path)

    assert second is first
    assert first.catalog_path() == catalog.path


def test_the_viewer_follows_a_new_default_catalog(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    window = _viewer(qtbot, render_catalog_manager, render_catalog_manager.default_catalog_path())
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)

    render_catalog_manager.set_default_catalog(catalog.path)

    assert window.catalog_path() == catalog.path


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
    source = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    _relabel_person(source.path, "Pedestrian")
    window = _viewer(qtbot, render_catalog_manager, source.path)
    monkeypatch.setattr(NewCatalogDialog, "exec", lambda self: self.name_edit.setText("Night") or 1)

    _button(window, "catalogViewerNew").click()

    assert window.catalog_path().name == "night.json"
    assert "Pedestrian" in window.sheet_titles()
    assert source.path.exists()


def test_delete_removes_the_catalog_after_asking_and_shows_the_default(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    monkeypatch: pytest.MonkeyPatch,
    small_catalog_path: Path,
) -> None:
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    render_catalog_manager.set_default_catalog(catalog.path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: answers.pop(0))

    _button(window, "catalogViewerDelete").click()
    assert catalog.path.exists()
    _button(window, "catalogViewerDelete").click()

    assert not catalog.path.exists()
    assert window.catalog_path() == render_catalog_manager.built_in_catalog_path()
    assert not _button(window, "catalogViewerDelete").isEnabled()


def test_a_broken_catalog_can_be_deleted_but_not_copied(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    catalog.path.write_text("{ not json", encoding="utf-8")

    window = _viewer(qtbot, render_catalog_manager, catalog.path)

    assert not _button(window, "catalogViewerNew").isEnabled()
    assert _button(window, "catalogViewerDelete").isEnabled()
    assert not _button(window, "catalogViewerApplyToAll").isEnabled()
    assert not _button(window, "catalogViewerUseAsDefault").isEnabled()


def test_the_catalog_shown_can_be_applied_to_every_view_and_made_the_default(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    selection = render_catalog_manager.create_selection()
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    assert selection.active_catalog_path() != catalog.path

    _button(window, "catalogViewerApplyToAll").click()

    assert selection.active_catalog_path() == catalog.path
    assert render_catalog_manager.default_catalog_path() != catalog.path

    _button(window, "catalogViewerUseAsDefault").click()

    assert render_catalog_manager.default_catalog_path() == catalog.path
    assert window.catalog_path() == catalog.path
    assert not _button(window, "catalogViewerUseAsDefault").isEnabled()


def test_theme_changes_preserve_live_and_error_status(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    from PySide6.QtGui import QColor, QPalette

    from ax_devil.modules.chrome.theme import StatusColor

    catalog = render_catalog_manager.create_catalog("Appearance", base_catalog_path=small_catalog_path)
    window = _viewer(qtbot, render_catalog_manager, catalog.path)
    for broken in (False, True):
        if broken:
            catalog.path.write_text("{}")
            window.show_catalog(catalog.path)
        for dark in (False, True):
            palette = QPalette(window.palette())
            palette.setColor(QPalette.ColorRole.Text, QColor("white" if dark else "black"))
            palette.setColor(QPalette.ColorRole.Window, QColor("black" if dark else "white"))
            window.setPalette(palette)
            expected = StatusColor.ERROR if broken else StatusColor.SUCCESS
            QApplication.processEvents()  # Restyling runs on the next event-loop turn.
            assert window._state.palette().color(window._state.foregroundRole()) == expected.color(window.palette())


def test_a_default_catalog_that_breaks_stays_on_screen_with_its_error(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, small_catalog_path: Path
) -> None:
    catalog = render_catalog_manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    render_catalog_manager.set_default_catalog(catalog.path)
    window = _viewer(qtbot, render_catalog_manager, render_catalog_manager.listing().chosen_default_path)

    catalog.path.write_text("{ not json", encoding="utf-8")
    qtbot.waitUntil(lambda: window.error() is not None, timeout=3000)

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
