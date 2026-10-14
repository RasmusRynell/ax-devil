from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveOverlayMode,
    LiveRTSPStreamSpec,
    LiveStreamItem,
    LiveVideoContent,
    OverlayContent,
    OverlayFile,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    RecentWorkspaces,
    SeekableVideoContent,
    VideoItem,
    Workspace,
    WorkspaceBackup,
    WorkspaceItem,
    save_workspace,
)
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.split_view import LeafContainer, SplitView
from ax_devil.modules.workspace.ui.workspace_prompts import WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from ax_devil.modules.workspace.ui.workspace_view import WorkspaceView
from tests.helpers.shortcuts import make_shortcut_manager
from tests.helpers.widgets import required_child
from tests.helpers.workspace import DummyViewer, FakePrompts, FakeResolutionContext, content_item, inline_resolver

_OFFLINE_VIEWER = "ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget"
_LIVE_VIEWER = "ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget"


def _item_rows(workspace_session: WorkspaceSession) -> list[QTreeWidgetItem]:
    """Return the tree items of the workspace's items, in section order, skipping the section headings."""
    tree = _tree(workspace_session)
    sections = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    return [section.child(index) for section in sections if section for index in range(section.childCount())]


def _required_item_row(workspace_session: WorkspaceSession, index: int = 0) -> QTreeWidgetItem:
    return _item_rows(workspace_session)[index]


def _center(session: WorkspaceSession) -> SplitView:
    center = session.widget().findChild(SplitView)
    assert center is not None
    return center


def _browser(session: WorkspaceSession) -> WorkspaceView:
    browser = session.widget().findChild(WorkspaceView)
    assert browser is not None
    return browser


def _tree(session: WorkspaceSession) -> QTreeWidget:
    tree: QTreeWidget | None = _browser(session).findChild(QTreeWidget)
    assert tree is not None
    return tree


def _row(item: QTreeWidgetItem) -> WorkspaceBrowserRow:
    return cast(WorkspaceBrowserRow, item.data(0, ROW_ROLE))


def _is_open(item: QTreeWidgetItem) -> bool:
    return _row(item).is_open is True


def _store(session: WorkspaceSession) -> WorkspaceStore:
    return session._workspace_store


def _focused(session: WorkspaceSession) -> DummyViewer:
    viewer = session.focused_widget()
    assert isinstance(viewer, DummyViewer)
    return viewer


class _FailingAttachViewer(DummyViewer):
    """Viewer test double that fails during deferred workspace startup."""

    def on_workspace_attached(self) -> None:
        """Raise a deferred startup failure."""
        raise RuntimeError("Failed to load local media: boom")


def _make_video(name: str, *, with_overlay: bool = False) -> SeekableVideoContent:
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


def _make_live_video(name: str) -> LiveVideoContent:
    return LiveVideoContent(
        display_name=name,
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass"),
        overlays=(),
    )


def _make_playlist(name: str = "Playlist") -> PlaylistContent:
    return PlaylistContent(
        display_name=name,
        entries=(
            PlaylistEntry(lanes=_make_video("first.mp4").standalone_lanes(), default_considered=True),
            PlaylistEntry(lanes=_make_video("second.mp4").standalone_lanes(), default_considered=True),
        ),
    )


def _session(
    render_catalog_manager: SceneRenderCatalogManager,
    tmp_path: Path,
    context: FakeResolutionContext | None = None,
    prompts: WorkspacePrompts | None = None,
) -> WorkspaceSession:
    """Return a session whose backup, recents, and prompts never touch the user's files or desktop."""
    return WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=ItemResolver(context or default_resolution_context(), in_background=False),
        backup=WorkspaceBackup(lambda: tmp_path / "state" / "workspace-backup.json"),
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "state" / "recent-workspaces.json"),
        prompts=prompts or FakePrompts(),
    )


def _video_item(tmp_path: Path, name: str) -> VideoItem:
    path = tmp_path / name
    path.write_bytes(b"")
    return VideoItem(video=path)


@pytest.fixture()
def workspace_session(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> WorkspaceSession:
    session = _session(render_catalog_manager, tmp_path)
    widget = session.widget()
    qtbot.addWidget(widget)
    widget.show()
    return session


def test_removing_content_in_the_sidebar_closes_every_viewer_of_its_item(
    workspace_session: WorkspaceSession,
) -> None:
    browser = _browser(workspace_session)

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("duplicate"))])
        [content] = _store(workspace_session).contents()
        browser.tree.content_open_to_side_requested.emit(content, 0)
        assert _center(workspace_session).get_widget_count() == 2

        browser.tree.item_remove_requested.emit(content.item_id)

    assert _center(workspace_session).get_widget_count() == 0
    assert _store(workspace_session).workspace.items == ()
    assert _tree(workspace_session).topLevelItemCount() == 0


def test_views_follow_the_rows_and_open_and_close_content_through_the_controller(
    workspace_session: WorkspaceSession,
) -> None:
    """A view of part of the workspace reads the same rows as the browser and can open and close an item's viewers."""
    controller = workspace_session._workspace_controller
    announced: list[object] = []
    controller.sections_changed.connect(announced.append)

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("clip"))])
        assert announced and announced[-1] == controller.sections()
        [row] = [row for section in controller.sections() for row in section.rows]
        assert row.is_open

        controller.close_item_viewers(row.item.id if row.item else "")
        assert _center(workspace_session).get_widget_count() == 0
        [row] = [row for section in controller.sections() for row in section.rows]
        assert not row.is_open

        assert row.activation_target is not None
        controller.open_content(*row.activation_target)
        assert _center(workspace_session).get_widget_count() == 1


class _TwoPlaylists:
    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        return [_make_playlist("train"), _make_playlist("test")]


class _NamedPlaylist:
    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        return [_make_playlist(str(settings["name"]))]


class _GrowingPlaylist:
    """A playlist that gains an entry on every resolve."""

    def __init__(self) -> None:
        self.clips = ["first.mp4"]

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        entries = tuple(PlaylistEntry(_make_video(clip).standalone_lanes(), True) for clip in self.clips)
        self.clips.append(f"clip{len(self.clips)}.mp4")
        return [PlaylistContent(display_name="Runs", entries=entries)]


def test_a_rescan_updates_the_rows_and_leaves_open_viewers_as_they_are(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = _session(render_catalog_manager, tmp_path, FakeResolutionContext(resolvers={"runs": _GrowingPlaylist()}))
    qtbot.addWidget(session.widget())
    runs = PlaylistItem(label="Runs", resolver="runs")

    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([runs])
        viewer = _focused(session)
        shown = viewer.payload
        _store(session).rescan_item(runs.id)

    assert _center(session).get_widget_count() == 1
    assert viewer.payload is shown
    assert _required_item_row(session).childCount() == 2


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
        assert _center(session).get_widget_count() == 1

        _store(session).open_workspace(replacement)

    assert _center(session).get_widget_count() == 0
    assert [content.display_name for content in _store(session).contents()] == ["New runs"]


def test_removing_one_content_of_an_item_removes_the_whole_item(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = _session(render_catalog_manager, tmp_path, FakeResolutionContext(resolvers={"runs": _TwoPlaylists()}))
    qtbot.addWidget(session.widget())
    kept = content_item(_make_video("kept.mp4"))

    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([PlaylistItem(label="Runs", resolver="runs"), kept])
        assert len(_item_rows(session)) == 3
        _browser(session).tree.item_remove_requested.emit(_store(session).contents()[1].item_id)

    assert _store(session).workspace.items == (kept,)
    assert len(_item_rows(session)) == 1


def test_removing_a_video_item_leaves_a_playlist_of_the_same_video_open(
    workspace_session: WorkspaceSession,
) -> None:
    video = _make_video("shared.mp4")
    playlist = PlaylistContent(
        display_name="Playlist",
        entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
    )
    video_item = content_item(video)

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(playlist)])
        playlist_viewer = _focused(workspace_session)
        playlist_viewer.set_pinned(True)
        workspace_session.add_items([video_item])

        assert _center(workspace_session).get_widget_count() == 2
        _store(workspace_session).remove_item(video_item.id)

    assert _center(workspace_session).get_widget_count() == 1
    assert workspace_session.focused_widget() is playlist_viewer


def test_removing_playlist_leaves_standalone_underlying_video_viewer_open(
    workspace_session: WorkspaceSession,
) -> None:
    video = _make_video("shared.mp4")
    playlist_item = content_item(
        PlaylistContent(
            display_name="Playlist",
            entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
        )
    )

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video)])
        video_viewer = _focused(workspace_session)
        video_viewer.set_pinned(True)
        workspace_session.add_items([playlist_item])

        assert _center(workspace_session).get_widget_count() == 2
        _store(workspace_session).remove_item(playlist_item.id)

    assert _center(workspace_session).get_widget_count() == 1
    assert workspace_session.focused_widget() is video_viewer


@pytest.mark.parametrize("kind", ["video", "live_stream"])
def test_adding_an_item_opens_a_viewer_and_marks_its_row_open(
    workspace_session: WorkspaceSession, tmp_path: Path, kind: str
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    items: dict[str, WorkspaceItem] = {
        "video": VideoItem(label="clip.mp4", video=video),
        "live_stream": LiveStreamItem(label="Camera", host="camera.local", username="root"),
    }

    with patch(_OFFLINE_VIEWER, DummyViewer), patch(_LIVE_VIEWER, DummyViewer):
        workspace_session.add_items([items[kind]])

    assert _center(workspace_session).get_widget_count() == 1
    assert len(_item_rows(workspace_session)) == 1
    assert _is_open(_required_item_row(workspace_session))


def test_dropped_video_and_overlay_open_with_matching_handler(
    workspace_session: WorkspaceSession, tmp_path: Path
) -> None:
    """A dropped video with an overlay only one decoder reads opens directly."""
    video = tmp_path / "gate.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "gate.txt"
    overlay.write_text("1,1,0,0,1,1,1,-1,-1,-1\n")

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.open_files([overlay, video])

    content = _focused(workspace_session).payload
    assert isinstance(content, SeekableVideoContent)
    assert content.source_spec.path == video
    assert [o.source_spec for o in content.overlays] == [FileOverlaySourceSpec(path=overlay, handler_type="MOT_FILE")]


def test_dropped_videos_are_added_in_drop_order_and_the_first_opens(
    workspace_session: WorkspaceSession, tmp_path: Path
) -> None:
    first, second = _video_item(tmp_path, "first.mp4"), _video_item(tmp_path, "second.mp4")

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.open_files([first.video, second.video])

    assert [item.video for item in cast(tuple[VideoItem, ...], _store(workspace_session).workspace.items)] == [
        first.video,
        second.video,
    ]
    assert _focused(workspace_session).get_display_name() == "first.mp4"


def test_dropped_overlay_read_by_several_decoders_asks_for_the_handler(
    workspace_session: WorkspaceSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambiguous drops ask in a real prefilled dialog; cancelling adds nothing and releases each dialog."""
    video = tmp_path / "gate.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "scene.jsonl"
    overlay.write_text("{}\n")
    asked: list[list[str]] = []
    destroyed: list[bool] = []
    original_exec = AddVideoDialog.exec

    def cancel(dialog: AddVideoDialog) -> int:
        asked.append([edit.text() for edit in dialog.findChildren(QLineEdit) if edit.text()])
        dialog.destroyed.connect(lambda: destroyed.append(True))
        QTimer.singleShot(0, dialog.reject)
        return int(original_exec(dialog))

    monkeypatch.setattr(AddVideoDialog, "exec", cancel)
    for _ in range(3):
        workspace_session.open_files([video, overlay])
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert asked == [[str(video), str(overlay)]] * 3
    assert destroyed == [True] * 3
    assert workspace_session.widget().findChildren(AddVideoDialog) == []
    assert _store(workspace_session).workspace.items == ()
    assert _tree(workspace_session).topLevelItemCount() == 0


@pytest.mark.parametrize("kind", ["video", "live_stream"])
def test_an_item_that_cannot_open_stays_in_the_workspace_and_the_user_is_told(
    workspace_session: WorkspaceSession, tmp_path: Path, kind: str
) -> None:
    video = tmp_path / "clip.mp4"
    overlay = tmp_path / "clip.json"
    video.write_bytes(b"")
    overlay.write_text("{}")
    items: dict[str, WorkspaceItem] = {
        "video": VideoItem(label="Clip", video=video, overlays=(OverlayFile(overlay, "MISSING"),)),
        "live_stream": LiveStreamItem(label="Clip", host="camera.local", overlay_mode=LiveOverlayMode.RTSP),
    }

    with patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
        workspace_session.add_items([items[kind]])

    assert _store(workspace_session).workspace.items == (items[kind],)
    assert _center(workspace_session).get_widget_count() == 0
    [(parent, title, message)] = [call.args for call in warning.call_args_list]
    assert (parent, title) == (workspace_session.widget(), "Open Content")
    assert message.startswith("Clip: ")
    assert len(_item_rows(workspace_session)) == 1
    row = _row(_required_item_row(workspace_session))
    assert (row.label, row.unavailable_reason) == ("Clip", message.removeprefix("Clip: "))

    with patch("PySide6.QtWidgets.QMessageBox.warning") as again:
        _tree(workspace_session).itemDoubleClicked.emit(_required_item_row(workspace_session), 0)

    assert [call.args[1:] for call in again.call_args_list] == [("Open Content", message)], "a double click says why"
    assert _center(workspace_session).get_widget_count() == 0


def test_adding_live_content_auto_opens_live_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    content = _make_live_video("live")

    with patch(_LIVE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(content)])

    assert _focused(workspace_session).payload is _store(workspace_session).contents()[0]
    assert _is_open(_required_item_row(workspace_session))


def test_viewer_current_item_change_updates_browser_open_indicator(
    workspace_session: WorkspaceSession,
) -> None:
    playlist = _make_playlist()

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(playlist)])

    viewer = _focused(workspace_session)
    top_item = _required_item_row(workspace_session)
    assert top_item.isExpanded()
    assert _is_open(required_child(top_item, 0))
    assert not _is_open(required_child(top_item, 1))

    viewer.start_index = 1
    viewer.on_screen_item_changed.emit(viewer.current_on_screen_item())

    top_item = _required_item_row(workspace_session)
    assert not _is_open(required_child(top_item, 0))
    assert _is_open(required_child(top_item, 1))


def test_focusing_open_viewer_preserves_open_indicators(
    workspace_session: WorkspaceSession,
) -> None:
    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("first.mp4"))])
        first_widget = _focused(workspace_session)
        first_widget.set_pinned(True)
        workspace_session.add_items([content_item(_make_video("second.mp4"))])

    assert _is_open(_required_item_row(workspace_session, 0))
    assert _is_open(_required_item_row(workspace_session, 1))

    QTest.mouseClick(first_widget, Qt.MouseButton.LeftButton)

    assert workspace_session.focused_widget() is first_widget
    assert _is_open(_required_item_row(workspace_session, 0))
    assert _is_open(_required_item_row(workspace_session, 1))


@pytest.mark.parametrize("failure", ["construct", "attach"])
def test_open_failure_shows_error_and_keeps_existing_viewer(workspace_session: WorkspaceSession, failure: str) -> None:
    """A viewer that fails to construct or to start leaves the existing preview pane open and running."""
    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("existing"))])
    existing = _focused(workspace_session)

    broken_viewer = (
        patch(_OFFLINE_VIEWER, side_effect=RuntimeError("Failed to load local media: boom"))
        if failure == "construct"
        else patch(_OFFLINE_VIEWER, _FailingAttachViewer)
    )
    with broken_viewer, patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
        workspace_session.add_items([content_item(_make_video("broken"))])

    assert _center(workspace_session).get_widget_count() == 1
    assert workspace_session.focused_widget() is existing
    assert not existing.cleaned_up
    assert _is_open(_required_item_row(workspace_session, 0))
    warning.assert_called_once_with(workspace_session.widget(), "Open Content", "Failed to load local media: boom")


def test_replaced_viewer_stops_receiving_consideration_updates(
    workspace_session: WorkspaceSession,
) -> None:
    first = _make_video("first.mp4")
    second = _make_video("second.mp4", with_overlay=True)

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(first)])
        first_widget = _focused(workspace_session)
        workspace_session.add_items([content_item(second)])
        second_widget = _focused(workspace_session)

    stored_second = _store(workspace_session).contents()[1]
    item_ref = ConsiderationItemRef.video_lane(stored_second.content_id, 0)
    _browser(workspace_session).tree.row_consideration_changed.emit(item_ref, False)

    assert first_widget.cleaned_up
    assert first_widget.refresh_calls == []
    assert second_widget.refresh_calls == [(item_ref, False)]


def test_open_to_side_keeps_preview_viewer_and_next_open_replaces_only_the_preview(
    workspace_session: WorkspaceSession,
) -> None:
    first = _make_video("first.mp4")
    second = _make_video("second.mp4")
    browser = _browser(workspace_session)

    def shown_names() -> list[str]:
        leaves = _center(workspace_session).findChildren(LeafContainer)
        return sorted(widget.get_display_name() for leaf in leaves if (widget := leaf.get_widget()) is not None)

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(first)])
        workspace_session.add_items([content_item(second)])
        assert shown_names() == ["second.mp4"]

        stored_first = _store(workspace_session).contents()[0]
        browser.tree.content_open_to_side_requested.emit(stored_first, 0)
        assert shown_names() == ["first.mp4", "second.mp4"]

        browser.tree.content_activated.emit(stored_first, 0)

    assert shown_names() == ["first.mp4", "first.mp4"]
    browser.tree.item_remove_requested.emit(stored_first.item_id)
    assert _center(workspace_session).get_widget_count() == 0


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

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("a"))])
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
    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("b"))])
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()

    window.toggle_sidebar()
    QCoreApplication.processEvents()
    assert window.is_sidebar_shown() and splitter.sizes()[0] == width

    # Dragged wider, it remembers the width; dragged closed, it stays closed until toggled back.
    splitter.moveSplitter(width + 60, 1)
    assert splitter.sizes()[0] == width + 60
    splitter.moveSplitter(0, 1)
    assert not window.is_sidebar_shown()
    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("c"))])
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()
    window.toggle_sidebar()
    assert window.is_sidebar_shown() and splitter.sizes()[0] == width + 60


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
    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("a"))])
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


def test_restored_workspace_lists_its_items_and_opens_no_viewer(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    kept = _session(render_catalog_manager, tmp_path)
    qtbot.addWidget(kept.widget())
    clip = _video_item(tmp_path, "clip.mp4")
    with patch(_OFFLINE_VIEWER, DummyViewer):
        kept.add_items([clip])
    kept.lifecycle.keep_workspace()

    restored = _session(render_catalog_manager, tmp_path)
    qtbot.addWidget(restored.widget())
    restored.lifecycle.launch()

    assert _store(restored).workspace.items == (clip,)
    assert len(_item_rows(restored)) == 1
    assert _center(restored).get_widget_count() == 0


def test_saved_workspace_is_listed_on_the_start_panel(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = _session(render_catalog_manager, tmp_path, prompts=FakePrompts(save_path=tmp_path / "Lot"))
    qtbot.addWidget(session.widget())
    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([_video_item(tmp_path, "lot.mp4")])

    assert session.lifecycle.save_workspace()

    saved = tmp_path / "Lot.ax-devil.workspace"
    assert [entry.path for entry in session.lifecycle.recent_workspaces()] == [saved]
    assert [row.path for row in session._start_panel.recent_rows()] == [saved]


def test_renaming_an_item_updates_its_row_and_open_viewer(workspace_session: WorkspaceSession, tmp_path: Path) -> None:
    clip = _video_item(tmp_path, "lot.mp4")
    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([clip])
    viewer = _focused(workspace_session)
    browser = _browser(workspace_session)

    browser.tree.item_rename_requested.emit(clip.id, "North gate")

    assert viewer._title_label.text() == "North gate"
    assert _row(_required_item_row(workspace_session)).label == "North gate"
    assert _store(workspace_session).is_modified

    browser.tree.item_rename_requested.emit(clip.id, "")

    assert viewer._title_label.text() == "lot.mp4"
    assert _row(_required_item_row(workspace_session)).label == "lot.mp4"


def test_kept_workspace_goes_to_the_storage_folder_saved_after_launch(
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

    session.lifecycle.keep_workspace()

    assert (tmp_path / "changed" / "workspace-backup.json").is_file()
    assert not (tmp_path / "launched" / "workspace-backup.json").exists()


def _press_row_action(session: WorkspaceSession, label: str) -> None:
    """Press the action button of the Workspace view's top-level row named *label*."""
    [item] = [item for item in _item_rows(session) if _row(item).label == label]
    widget = _tree(session).itemWidget(item, 0)
    assert widget is not None
    [button] = widget.findChildren(SmallButton)
    button.click()


def test_connect_and_disconnect_in_the_workspace_view_open_and_close_the_camera(
    workspace_session: WorkspaceSession,
) -> None:
    _store(workspace_session).replace_workspace(Workspace(items=(LiveStreamItem(label="Gate", host="camera.local"),)))
    with patch(_LIVE_VIEWER, DummyViewer):
        _press_row_action(workspace_session, "Gate")
        assert _center(workspace_session).get_widget_count() == 1
        assert _is_open(_required_item_row(workspace_session))

        _press_row_action(workspace_session, "Gate")

    assert _center(workspace_session).get_widget_count() == 0
    assert not _is_open(_required_item_row(workspace_session))


def test_rescan_in_the_workspace_view_resolves_the_playlist_again(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    class Growing:
        """A resolver whose playlist gains an entry each time it is resolved."""

        def __init__(self) -> None:
            self.entries = 0

        def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
            self.entries += 1
            lanes = _make_video("clip.mp4").standalone_lanes()
            return [PlaylistContent(display_name="Runs", entries=(PlaylistEntry(lanes, True),) * self.entries)]

    session = _session(render_catalog_manager, tmp_path, FakeResolutionContext(resolvers={"folder_pair": Growing()}))
    qtbot.addWidget(session.widget())
    session.widget().show()
    _store(session).replace_workspace(Workspace(items=(PlaylistItem(resolver="folder_pair"),)))
    assert _required_item_row(session).childCount() == 1

    _press_row_action(session, "Runs")

    assert _required_item_row(session).childCount() == 2
    assert _center(session).get_widget_count() == 0
