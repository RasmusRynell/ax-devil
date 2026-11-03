"""The workspace session and the rows, viewers and resolvers its tests share."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    RecentWorkspaces,
    SeekableVideoContent,
    VideoItem,
)
from ax_devil.modules.workspace.ui.browser_rows import RowAction, WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_prompts import WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from ax_devil.modules.workspace.ui.workspace_view import WorkspaceView
from tests.helpers.workspace import DummyViewer, FakePrompts, FakeResolutionContext

OFFLINE_VIEWER = "ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget"


LIVE_VIEWER = "ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget"


def item_rows(workspace_session: WorkspaceSession) -> list[QTreeWidgetItem]:
    """Return the tree items of the workspace's items, in section order, skipping the section headings."""
    tree = tree_of(workspace_session)
    sections = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    return [section.child(index) for section in sections if section for index in range(section.childCount())]


def required_item_row(workspace_session: WorkspaceSession, index: int = 0) -> QTreeWidgetItem:
    return item_rows(workspace_session)[index]


def center_of(session: WorkspaceSession) -> SplitView:
    center = session.widget().findChild(SplitView)
    assert center is not None
    return center


def browser_of(session: WorkspaceSession) -> WorkspaceView:
    browser = session.widget().findChild(WorkspaceView)
    assert browser is not None
    return browser


def tree_of(session: WorkspaceSession) -> QTreeWidget:
    tree: QTreeWidget | None = browser_of(session).findChild(QTreeWidget)
    assert tree is not None
    return tree


def row_of(item: QTreeWidgetItem) -> WorkspaceBrowserRow:
    return cast(WorkspaceBrowserRow, item.data(0, ROW_ROLE))


def item_is_open(item: QTreeWidgetItem) -> bool:
    return row_of(item).is_open is True


def store_of(session: WorkspaceSession) -> WorkspaceStore:
    return session._workspace_store


def ask_row_action(session: WorkspaceSession, item_id: str, action: RowAction) -> None:
    """Ask the sidebar's tree for *action* on the first row of the item *item_id*, as its menu or a key does."""
    row = next(
        row
        for section in session._workspace_controller.sections()
        for row in section.rows
        if row.item is not None and row.item.id == item_id
    )
    browser_of(session).tree.row_action_requested.emit(row, action)


def focused_viewer(session: WorkspaceSession) -> DummyViewer:
    viewer = session.focused_widget()
    assert isinstance(viewer, DummyViewer)
    return viewer


def video_content(name: str, *, with_overlay: bool = False) -> SeekableVideoContent:
    overlays = (
        (OverlayContent(display_name="overlay", source_spec=FileOverlaySourceSpec(Path("/tmp/o.txt"), "MOT_FILE")),)
        if with_overlay
        else ()
    )
    return SeekableVideoContent(
        display_name=name,
        source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")),
        overlays=overlays,
    )


def playlist_content(name: str = "Playlist") -> PlaylistContent:
    return PlaylistContent(
        display_name=name,
        entries=(
            PlaylistEntry(lanes=video_content("first.mp4").standalone_lanes(), default_considered=True),
            PlaylistEntry(lanes=video_content("second.mp4").standalone_lanes(), default_considered=True),
        ),
    )


def new_session(
    render_catalog_manager: SceneRenderCatalogManager,
    tmp_path: Path,
    context: FakeResolutionContext | None = None,
    prompts: WorkspacePrompts | None = None,
) -> WorkspaceSession:
    """Return a session whose unsaved copies, recents, and prompts never touch the user's files or desktop."""
    return WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=ItemResolver(context or default_resolution_context(), in_background=False),
        unsaved_folder=tmp_path / "state" / "unsaved-workspaces",
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "state" / "recent-workspaces.json"),
        prompts=prompts or FakePrompts(),
    )


def video_file_item(tmp_path: Path, name: str) -> VideoItem:
    path = tmp_path / name
    path.write_bytes(b"")
    return VideoItem(video=path)
