"""Right-clicking a viewer's header offers what the sidebar row of its item offers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import RecentWorkspaces, VideoItem
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_view import WorkspaceView
from tests.helpers.workspace import DummyViewer, FakePrompts

_OFFLINE_VIEWER = "ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget"


@pytest.fixture()
def session(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path) -> WorkspaceSession:
    """A shown session whose recents live under *tmp_path*."""
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=ItemResolver(default_resolution_context(), in_background=False),
        unsaved_folder=tmp_path / "state" / "unsaved-workspaces",
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "state" / "recent-workspaces.json"),
        prompts=FakePrompts(),
    )
    qtbot.addWidget(session.widget())
    session.widget().show()
    return session


def _show_menu(
    session: WorkspaceSession, trigger: Callable[[], None], act: Callable[[QMenu], None] | None = None
) -> list[tuple[str, bool, str]]:
    """Run *trigger*, which shows a menu, and return what the menu lists; *act* then does something with it closed."""
    listed: list[tuple[str, bool, str]] = []

    def inspect() -> None:
        menu = session.widget().findChildren(QMenu)[-1]
        listed.extend((entry.text(), entry.isEnabled(), entry.toolTip()) for entry in menu.actions())
        menu.close()
        if act is not None:
            act(menu)

    QTimer.singleShot(0, inspect)
    trigger()
    return listed


def _choose(menu: QMenu, text: str) -> None:
    next(entry for entry in menu.actions() if entry.text() == text).trigger()


def _open_clip(session: WorkspaceSession, tmp_path: Path) -> tuple[VideoItem, DummyViewer]:
    clip = VideoItem(video=tmp_path / "lot.mp4")
    (tmp_path / "lot.mp4").write_bytes(b"")
    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([clip])
    viewer = session.focused_widget()
    assert isinstance(viewer, DummyViewer)
    return clip, viewer


def _right_click_header(viewer: DummyViewer) -> None:
    header = viewer.get_header_widget()
    point = QPoint(4, 4)
    QApplication.sendEvent(header, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, header.mapToGlobal(point)))


def _right_click_sidebar_row(session: WorkspaceSession) -> None:
    browser = session.widget().findChild(WorkspaceView)
    assert browser is not None
    tree = browser.findChild(QTreeWidget)
    assert tree is not None
    section = tree.topLevelItem(0)
    assert section is not None
    row = section.child(0)
    assert row is not None
    tree.customContextMenuRequested.emit(tree.visualItemRect(row).center())


def _center(session: WorkspaceSession) -> SplitView:
    center = session.widget().findChild(SplitView)
    assert center is not None
    return center


def test_header_right_click_offers_what_the_sidebar_row_offers(session: WorkspaceSession, tmp_path: Path) -> None:
    _clip, viewer = _open_clip(session, tmp_path)

    sidebar = _show_menu(session, lambda: _right_click_sidebar_row(session))
    header = _show_menu(session, lambda: _right_click_header(viewer))

    assert header == sidebar
    enabled = {text for text, is_enabled, _tip in header if is_enabled}
    assert {"Information", "Export video", "Rename", "Remove"} <= enabled


def test_choosing_remove_from_the_header_menu_removes_the_item_and_closes_the_pane(
    session: WorkspaceSession, tmp_path: Path
) -> None:
    _clip, viewer = _open_clip(session, tmp_path)

    _show_menu(session, lambda: _right_click_header(viewer), lambda menu: _choose(menu, "Remove"))

    assert session._workspace_store.workspace.items == ()
    assert _center(session).get_widget_count() == 0
