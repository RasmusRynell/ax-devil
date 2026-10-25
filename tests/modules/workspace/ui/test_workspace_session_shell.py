"""The workspace session shell: sidebar, activity bar, and restoring unsaved changes at launch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QSplitter
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.banner import Banner
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.shortcuts import make_shortcut_manager
from tests.helpers.workspace import (
    DummyViewer,
    FakePrompts,
    content_item,
    inline_resolver,
    quit_app,
    recent_workspace_names,
)
from tests.helpers.workspace_session import (
    OFFLINE_VIEWER,
    center_of,
    item_rows,
    new_session,
    store_of,
    video_content,
    video_file_item,
)


def _sidebar_splitter(workspace_session: WorkspaceSession) -> QSplitter:
    splitter: QSplitter | None = workspace_session.widget().findChild(QSplitter, "WorkspaceSidebarSplitter")
    assert splitter is not None
    return splitter


def test_sidebar_swaps_start_panel_for_content_and_toggles_at_its_width(workspace_session: WorkspaceSession) -> None:
    """The sidebar shows the start panel while empty and the workspace's rows with content; the user can hide it."""
    window = workspace_session.widget()
    window.resize(1200, 700)
    window.show()
    QCoreApplication.processEvents()
    splitter = _sidebar_splitter(workspace_session)
    start_panel = workspace_session._start_panel
    assert window.is_sidebar_shown() and start_panel.isVisible()

    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video_content("a"))])
    QCoreApplication.processEvents()
    assert window.is_sidebar_shown() and not start_panel.isVisible()
    width = splitter.sizes()[0]
    assert 0 < width < 1200 // 2

    workspace_button = window._activity_bar.view_button("workspace")
    assert workspace_button.isChecked()
    workspace_button.click()
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown() and not workspace_button.isChecked()

    # Hidden by the user, it stays hidden when content changes.
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video_content("b"))])
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()

    window.toggle_sidebar()
    QCoreApplication.processEvents()
    assert window.is_sidebar_shown() and splitter.sizes()[0] == width

    # Dragged wider, it remembers the width; dragged closed and back out within one held drag, it reopens;
    # released closed, it stays closed until toggled back.
    handle = splitter.handle(1)
    grab = handle.rect().center()
    origin = handle.mapToGlobal(grab)

    def drag_to(dx: int) -> int:
        """Move the held mouse *dx* pixels from where it grabbed the handle; return the sidebar width."""
        QTest.mouseMove(handle, handle.mapFromGlobal(origin + QPoint(dx, 0)))
        return int(splitter.sizes()[0])

    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=grab)
    assert drag_to(60) > width
    assert drag_to(-2000) == 0
    reopened = drag_to(-30)
    assert 0 < reopened < width
    assert drag_to(-2000) == 0
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton)
    assert not window.is_sidebar_shown()
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video_content("c"))])
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()
    window.toggle_sidebar()
    assert window.is_sidebar_shown() and splitter.sizes()[0] == reopened


def test_activity_bar_buttons_trigger_render_catalogs_and_settings(workspace_session: WorkspaceSession) -> None:
    """The activity bar's action buttons trigger the same actions as the menus."""
    window = workspace_session.widget()
    manager = make_shortcut_manager()
    manager.install(window)
    workspace_session.set_shortcut_manager(manager)
    triggered: list[str] = []
    for action_id in ("view.render_catalogs", "app.settings"):
        manager.get_action(action_id).triggered.connect(
            lambda _=False, action_id=action_id: triggered.append(action_id)
        )

    for button in window._activity_bar._action_buttons:
        button.click()

    assert triggered == ["view.render_catalogs", "app.settings"]


@pytest.mark.usefixtures("restore_app_appearance")
def test_sidebar_default_width_follows_text_size_until_dragged(workspace_session: WorkspaceSession) -> None:
    """An undragged sidebar keeps its default width in characters when the text size changes; a dragged one stays."""
    from ax_devil.modules.chrome.theme import apply_text_size

    window = workspace_session.widget()
    window.resize(1200, 700)
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video_content("a"))])
    QCoreApplication.processEvents()
    splitter = _sidebar_splitter(workspace_session)
    apply_text_size(13)
    QCoreApplication.processEvents()
    small = splitter.sizes()[0]
    apply_text_size(21)
    QCoreApplication.processEvents()
    assert splitter.sizes()[0] > small

    splitter.moveSplitter(300, 1)
    dragged = splitter.sizes()[0]
    apply_text_size(13)
    QCoreApplication.processEvents()
    assert splitter.sizes()[0] == dragged


@pytest.mark.parametrize("saved", [True, False])
def test_launch_restores_unsaved_changes_with_a_banner_and_discard_returns_to_the_saved_file_or_the_start_panel(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path, saved: bool
) -> None:
    original = tmp_path / "Lot.ax-devil.workspace"
    quit_session = new_session(render_catalog_manager, tmp_path, prompts=FakePrompts(save_path=original))
    qtbot.addWidget(quit_session.widget())
    kept, edit = video_file_item(tmp_path, "kept.mp4"), video_file_item(tmp_path, "edit.mp4")
    with patch(OFFLINE_VIEWER, DummyViewer):
        if saved:
            quit_session.add_items([kept])
            quit_session.lifecycle.save_workspace()
        quit_session.add_items([edit])
    quit_app(quit_session.lifecycle)

    launched = new_session(render_catalog_manager, tmp_path)
    qtbot.addWidget(launched.widget())
    launched.lifecycle.launch()

    banner = launched.widget().findChild(Banner)
    assert banner is not None and not banner.isHidden()
    assert store_of(launched).is_modified
    assert store_of(launched).workspace.items == ((kept, edit) if saved else (edit,))
    assert len(item_rows(launched)) == (2 if saved else 1)
    assert center_of(launched).get_widget_count() == 0

    banner.action.click()

    assert banner.isHidden()
    assert not store_of(launched).is_modified
    assert store_of(launched).workspace.items == ((kept,) if saved else ())
    assert store_of(launched).workspace.path == (original if saved else None)
    assert [entry.path for entry in launched.lifecycle.recent_workspaces()] == ([original] if saved else [])
    assert list((tmp_path / "state" / "unsaved-workspaces").glob("*.ax-devil.workspace")) == []


def test_keeping_restored_unsaved_changes_only_hides_the_banner(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    quit_session = new_session(render_catalog_manager, tmp_path)
    qtbot.addWidget(quit_session.widget())
    edit = video_file_item(tmp_path, "edit.mp4")
    with patch(OFFLINE_VIEWER, DummyViewer):
        quit_session.add_items([edit])
    quit_app(quit_session.lifecycle)
    launched = new_session(render_catalog_manager, tmp_path)
    qtbot.addWidget(launched.widget())
    launched.lifecycle.launch()
    banner = launched.widget().findChild(Banner)
    assert banner is not None and banner.dismiss is not None

    banner.dismiss.click()

    assert banner.isHidden()
    assert store_of(launched).is_modified
    assert store_of(launched).workspace.items == (edit,)


def test_saved_workspace_is_listed_on_the_start_panel(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = new_session(render_catalog_manager, tmp_path, prompts=FakePrompts(save_path=tmp_path / "Lot"))
    qtbot.addWidget(session.widget())
    with patch(OFFLINE_VIEWER, DummyViewer):
        session.add_items([video_file_item(tmp_path, "lot.mp4")])

    assert session.lifecycle.save_workspace()

    saved = tmp_path / "Lot.ax-devil.workspace"
    assert [entry.path for entry in session.lifecycle.recent_workspaces()] == [saved]
    assert recent_workspace_names(session._start_panel) == ["Lot"]


def test_unsaved_workspace_goes_to_the_storage_folder_saved_after_launch(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    config = ConfigManager()

    def storage(folder: str) -> dict[str, str]:
        root = tmp_path / folder
        return {
            "base_dir": str(root),
            "cache_dir": str(root / "caches"),
            "logs_dir": str(root / "logs"),
            "render_catalogs_dir": str(root / "render_catalogs"),
        }

    config.set("storage", storage("launched"))
    config.activate_storage()
    config.set("storage", storage("changed"))
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=inline_resolver(),
        prompts=FakePrompts(),
    )
    qtbot.addWidget(session.widget())

    with patch(OFFLINE_VIEWER, DummyViewer):
        session.add_items([video_file_item(tmp_path, "lot.mp4")])

    session.lifecycle.keep_unsaved_changes()

    assert list((tmp_path / "changed" / "unsaved-workspaces").iterdir())
    assert not (tmp_path / "launched").exists() or not list((tmp_path / "launched").rglob("*.workspace"))
