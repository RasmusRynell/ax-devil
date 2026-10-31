"""Catalog-manager notifications update previews; file watching is covered by manager tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QTabBar
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.catalog_viewer.inspector import ObjectInspector
from ax_devil.modules.catalog_viewer.sheet_drawing import sheet_frame
from ax_devil.modules.catalog_viewer.sheets import sheets
from ax_devil.modules.catalog_viewer.viewer_registry import close_catalog_viewer, show_catalog_viewer
from ax_devil.modules.catalog_viewer.window import CatalogViewerWindow
from ax_devil.modules.object_inspection.fields_view import FieldsView
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogLoader,
    SceneRenderCatalogManager,
)
from ax_devil.modules.scene.rendering.catalog_loader import CatalogJsonDocument
from ax_devil.modules.video_player.engine.renderer import VideoFrameRenderer
from tests.catalog_helpers import (
    create_catalog,
    wait_for_catalogs,
)
from tests.helpers.catalog_viewer import error_banner as _banner
from tests.helpers.catalog_viewer import file_changed as _file_changed
from tests.helpers.catalog_viewer import open_viewer as _viewer
from tests.helpers.catalog_viewer import relabel_person as _relabel_person


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
