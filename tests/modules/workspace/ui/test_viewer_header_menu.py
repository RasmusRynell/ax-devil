"""Right-clicking a viewer's header acts on the Content and the viewer that were clicked."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import (
    FileVideoSourceSpec,
    PlaylistContent,
    PlaylistEntry,
    RecentWorkspaces,
    SeekableVideoContent,
    VideoItem,
)
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo, build_playlist_information
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.split_view import SplitView
from tests.helpers.workspace import DummyViewer, FakePrompts, content_item

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


def _center(session: WorkspaceSession) -> SplitView:
    center = session.widget().findChild(SplitView)
    assert center is not None
    return center


def test_choosing_remove_from_the_header_menu_removes_the_item_and_closes_the_pane(
    session: WorkspaceSession, tmp_path: Path
) -> None:
    _clip, viewer = _open_clip(session, tmp_path)

    _show_menu(session, lambda: _right_click_header(viewer), lambda menu: _choose(menu, "Remove"))

    assert session._workspace_store.workspace.items == ()
    assert _center(session).get_widget_count() == 0


class _ExportingViewer(DummyViewer):
    """A viewer that records the exports it is asked for."""

    exported: list[DummyViewer] = []

    def export_video(self) -> None:
        self.exported.append(self)


class _RecordingInfoDialog:
    """Stands in for the Information dialog and records the title it was asked to show."""

    titles: list[str] = []

    def __init__(self, info: WorkspaceItemInfo, _parent: object = None) -> None:
        self.titles.append(info.title)

    def __enter__(self) -> _RecordingInfoDialog:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def exec(self) -> int:
        return 0


def _choose_from_header(session: WorkspaceSession, viewer: DummyViewer, text: str) -> None:
    _show_menu(session, lambda: _right_click_header(viewer), lambda menu: _choose(menu, text))


def _playlist(name: str) -> PlaylistContent:
    lanes = SeekableVideoContent(
        display_name=name, source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}.mp4"))
    ).standalone_lanes()
    return PlaylistContent(display_name=name, entries=(PlaylistEntry(lanes, True),))


def test_export_from_a_header_exports_that_viewer_when_the_same_video_is_open_twice(
    session: WorkspaceSession, tmp_path: Path
) -> None:
    (tmp_path / "lot.mp4").write_bytes(b"")
    clip = VideoItem(video=tmp_path / "lot.mp4")
    _ExportingViewer.exported.clear()
    with patch(_OFFLINE_VIEWER, _ExportingViewer):
        session.add_items([clip])
        session._workspace_controller.open_content(session._workspace_store.contents()[0], to_side=True)
    viewers = session.widget().findChildren(_ExportingViewer)
    assert len(viewers) == 2

    for viewer in viewers:
        _ExportingViewer.exported.clear()
        _choose_from_header(session, viewer, "Export video")
        assert _ExportingViewer.exported == [viewer]


def test_a_header_menu_acts_on_the_playlist_its_viewer_shows_when_an_item_resolves_to_several(
    session: WorkspaceSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    playlists = (_playlist("train"), _playlist("test"))
    _RecordingInfoDialog.titles.clear()
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.workspace_controller.WorkspaceItemInfoDialog", _RecordingInfoDialog
    )
    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([content_item(*playlists)])
        for content in session._workspace_store.contents()[1:]:
            session._workspace_controller.open_content(content, to_side=True)
    viewers = session.widget().findChildren(DummyViewer)
    assert len(viewers) == 2

    for viewer in viewers:
        _RecordingInfoDialog.titles.clear()
        _choose_from_header(session, viewer, "Information")
        assert isinstance(viewer.payload, PlaylistContent)
        assert _RecordingInfoDialog.titles == [build_playlist_information(viewer.payload).title]
