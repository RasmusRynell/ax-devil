"""The workspace session keeps viewers and sidebar rows in step with the Workspace's items."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import (
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    RecentWorkspaces,
    Workspace,
    save_workspace,
)
from ax_devil.modules.workspace.ui.browser_rows import RowAction
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.workspace import (
    DummyViewer,
    FakeResolutionContext,
    FakeResolver,
    content_item,
    inline_resolver,
    wait_for_background_work,
)
from tests.helpers.workspace_session import (
    OFFLINE_VIEWER,
    ask_row_action,
    center_of,
    focused_viewer,
    item_rows,
    new_session,
    playlist_content,
    required_item_row,
    store_of,
    tree_of,
    video_content,
)


def test_removing_content_in_the_sidebar_closes_every_viewer_of_its_item(
    workspace_session: WorkspaceSession,
) -> None:

    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video_content("duplicate"))])
        [content] = store_of(workspace_session).contents()
        ask_row_action(workspace_session, content.item_id, RowAction.OPEN_TO_SIDE)
        assert center_of(workspace_session).get_widget_count() == 2

        ask_row_action(workspace_session, content.item_id, RowAction.REMOVE)

    assert center_of(workspace_session).get_widget_count() == 0
    assert store_of(workspace_session).workspace.items == ()
    assert tree_of(workspace_session).topLevelItemCount() == 0


def test_views_follow_the_rows_and_open_and_close_content_through_the_controller(
    workspace_session: WorkspaceSession,
) -> None:
    """A view of part of the workspace reads the same rows as the browser and can open and close an item's viewers."""
    controller = workspace_session._workspace_controller
    announced: list[object] = []
    controller.sections_changed.connect(announced.append)

    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video_content("clip"))])
        assert announced and announced[-1] == controller.sections()
        [row] = [row for section in controller.sections() for row in section.rows]
        assert row.is_open

        controller.close_item_viewers(row.item.id if row.item else "")
        assert center_of(workspace_session).get_widget_count() == 0
        [row] = [row for section in controller.sections() for row in section.rows]
        assert not row.is_open

        assert row.activation_target is not None
        controller.perform_row_action(row, RowAction.OPEN)
        assert center_of(workspace_session).get_widget_count() == 1


class _TwoPlaylists(FakeResolver):
    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        return [playlist_content("train"), playlist_content("test")]


class _NamedPlaylist(FakeResolver):
    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        return [playlist_content(str(settings["name"]))]


class _ClipsFolder(FakeResolver):
    """A playlist with an entry for each ``.mp4`` file in a folder, which it asks to have watched."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        clips = sorted(path.name for path in self.folder.glob("*.mp4"))
        entries = tuple(PlaylistEntry(video_content(clip).standalone_lanes(), True) for clip in clips)
        return [PlaylistContent(display_name="Runs", entries=entries)]

    def source_paths(self, settings: PlaylistSettings, token: CancelToken) -> SourcePaths:
        return SourcePaths(folders=frozenset({self.folder}))


def test_files_regenerated_on_disk_update_the_rows_and_leave_open_viewers_as_they_are(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    folder = tmp_path / "runs"
    folder.mkdir()
    (folder / "first.mp4").touch()
    session = new_session(
        render_catalog_manager, tmp_path, FakeResolutionContext(resolvers={"runs": _ClipsFolder(folder)})
    )
    qtbot.addWidget(session.widget())
    threads = set(threading.enumerate())

    with patch(OFFLINE_VIEWER, DummyViewer):
        session.add_items([PlaylistItem(label="Runs", resolver="runs")])
        viewer = focused_viewer(session)
        shown = viewer.payload
        wait_for_background_work(qtbot, threads)  # The folder is now watched; later files are changes.
        (folder / "second.mp4").touch()

        qtbot.waitUntil(lambda: required_item_row(session).childCount() == 2, timeout=10_000)

    assert center_of(session).get_widget_count() == 1
    assert viewer.payload is shown


def test_replacing_the_workspace_closes_viewers_even_when_an_item_id_is_kept(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=inline_resolver(FakeResolutionContext(resolvers={"named": _NamedPlaylist()})),
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "recent-workspaces.json"),
    )
    qtbot.addWidget(session.widget())
    replacement = tmp_path / "replacement.ax-devil.workspace"
    save_workspace(
        Workspace(items=(PlaylistItem(id="runs", label="New runs", resolver="named", settings={"name": "new"}),)),
        replacement,
    )

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        session.add_items([PlaylistItem(id="runs", label="Runs", resolver="named", settings={"name": "old"})])
        assert center_of(session).get_widget_count() == 1

        store_of(session).open_workspace(replacement)

    assert center_of(session).get_widget_count() == 0
    assert [content.display_name for content in store_of(session).contents()] == ["New runs"]


def test_removing_one_content_of_an_item_removes_the_whole_item(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = new_session(render_catalog_manager, tmp_path, FakeResolutionContext(resolvers={"runs": _TwoPlaylists()}))
    qtbot.addWidget(session.widget())
    kept = content_item(video_content("kept.mp4"))

    with patch(OFFLINE_VIEWER, DummyViewer):
        session.add_items([PlaylistItem(label="Runs", resolver="runs"), kept])
        assert len(item_rows(session)) == 3
        ask_row_action(session, store_of(session).contents()[1].item_id, RowAction.REMOVE)

    assert store_of(session).workspace.items == (kept,)
    assert len(item_rows(session)) == 1


@pytest.mark.parametrize("remove", ["video", "playlist"])
def test_removing_one_of_a_video_and_a_playlist_of_it_leaves_the_other_viewer_open(
    workspace_session: WorkspaceSession, remove: str
) -> None:
    video = video_content("shared.mp4")
    playlist = PlaylistContent(
        display_name="Playlist",
        entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
    )
    items = {"video": content_item(video), "playlist": content_item(playlist)}
    removed = items[remove]
    (kept,) = (item for item in items.values() if item is not removed)

    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([kept])
        kept_viewer = focused_viewer(workspace_session)
        kept_viewer.set_pinned(True)
        workspace_session.add_items([removed])

        assert center_of(workspace_session).get_widget_count() == 2
        store_of(workspace_session).remove_item(removed.id)

    assert center_of(workspace_session).get_widget_count() == 1
    assert workspace_session.focused_widget() is kept_viewer
