from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QTreeWidgetItem
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveOverlayMode,
    LiveRTSPStreamSpec,
    LiveStreamItem,
    LiveVideoContent,
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
from ax_devil.modules.workspace.ui.content_browser import TREE_LABEL_COLUMN, TREE_ROW_ROLE
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.workspace_prompts import WorkspacePrompts
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.workspace import DummyViewer, FakePrompts, FakeResolutionContext, content_item


def _required_top_item(workspace_session: WorkspaceSession, index: int = 0) -> QTreeWidgetItem:
    item = workspace_session._content_browser._tree.topLevelItem(index)
    assert item is not None
    return item


def _is_open(item: QTreeWidgetItem) -> bool:
    return cast(WorkspaceBrowserRow, item.data(TREE_LABEL_COLUMN, TREE_ROW_ROLE)).is_open is True


def _required_child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    child = item.child(index)
    assert child is not None
    return child


class _FailingAttachViewer(DummyViewer):
    """Viewer test double that fails during deferred workspace startup."""

    def on_workspace_attached(self) -> None:
        """Raise a deferred startup failure."""
        raise RuntimeError("Failed to load local media: boom")


def _make_video(name: str) -> SeekableVideoContent:
    return SeekableVideoContent(
        display_name=name,
        source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")),
        overlays=(),
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
        context=context,
        backup=WorkspaceBackup(tmp_path / "state" / "workspace-backup.json"),
        recent_workspaces=RecentWorkspaces(tmp_path / "state" / "recent-workspaces.json"),
        prompts=prompts or FakePrompts(),
    )


@pytest.fixture()
def workspace_session(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> WorkspaceSession:
    session = _session(render_catalog_manager, tmp_path)
    widget = session.widget()
    qtbot.addWidget(widget)
    widget.show()
    return session


def _store(workspace_session: WorkspaceSession) -> WorkspaceStore:
    return workspace_session._workspace_store


def test_removing_content_in_the_sidebar_closes_every_viewer_of_its_item(
    workspace_session: WorkspaceSession,
) -> None:
    browser = workspace_session._content_browser

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(_make_video("duplicate"))])
        [content] = _store(workspace_session).contents()
        browser.content_open_to_side_requested.emit(content, 0)
        assert workspace_session._center_area.get_widget_count() == 2

        browser.item_remove_requested.emit(content.item_id)

    assert workspace_session._center_area.get_widget_count() == 0
    assert _store(workspace_session).workspace.items == ()
    assert workspace_session._content_browser._tree.topLevelItemCount() == 0


class _TwoPlaylists:
    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        return [_make_playlist("train"), _make_playlist("test")]


class _NamedPlaylist:
    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        return [_make_playlist(str(settings["name"]))]


def test_replacing_the_workspace_closes_viewers_even_when_an_item_id_is_kept(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        context=FakeResolutionContext(resolvers={"named": _NamedPlaylist()}),
        recent_videos=RecentVideos(tmp_path / "recent-videos.json"),
    )
    qtbot.addWidget(session.widget())
    replacement = tmp_path / "replacement.ax-devil.workspace"
    save_workspace(
        Workspace(items=(PlaylistItem(id="runs", label="New runs", resolver="named", settings={"name": "new"}),)),
        replacement,
    )

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        session.add_items([PlaylistItem(id="runs", label="Runs", resolver="named", settings={"name": "old"})])
        assert session._center_area.get_widget_count() == 1

        _store(session).open_workspace(replacement)

    assert session._center_area.get_widget_count() == 0
    assert [content.display_name for content in _store(session).contents()] == ["New runs"]


def test_removing_one_content_of_an_item_removes_the_whole_item(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = _session(render_catalog_manager, tmp_path, FakeResolutionContext(resolvers={"runs": _TwoPlaylists()}))
    qtbot.addWidget(session.widget())
    kept = content_item(_make_video("kept.mp4"))

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        session.add_items([PlaylistItem(label="Runs", resolver="runs"), kept])
        assert session._content_browser._tree.topLevelItemCount() == 3
        session._content_browser.item_remove_requested.emit(_store(session).contents()[1].item_id)

    assert _store(session).workspace.items == (kept,)
    assert session._content_browser._tree.topLevelItemCount() == 1


def test_removing_a_video_item_leaves_a_playlist_of_the_same_video_open(
    workspace_session: WorkspaceSession,
) -> None:
    video = _make_video("shared.mp4")
    playlist = PlaylistContent(
        display_name="Playlist",
        entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
    )
    video_item = content_item(video)

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(playlist)])
        playlist_viewer = workspace_session._center_area.get_focused_widget()
        assert playlist_viewer is not None
        playlist_viewer.set_pinned(True)
        workspace_session.add_items([video_item])

        assert workspace_session._center_area.get_widget_count() == 2
        _store(workspace_session).remove_item(video_item.id)

    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._center_area.get_focused_widget() is playlist_viewer


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

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(video)])
        video_viewer = workspace_session._center_area.get_focused_widget()
        assert video_viewer is not None
        video_viewer.set_pinned(True)
        workspace_session.add_items([playlist_item])

        assert workspace_session._center_area.get_widget_count() == 2
        _store(workspace_session).remove_item(playlist_item.id)

    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._center_area.get_focused_widget() is video_viewer


def test_adding_content_auto_opens_viewers(
    workspace_session: WorkspaceSession,
) -> None:
    content = _make_video("existing")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(content)])

    assert workspace_session._center_area.get_widget_count() == 1
    viewer = workspace_session._center_area.get_focused_widget()
    assert isinstance(viewer, DummyViewer)
    assert viewer.consideration_query is _store(workspace_session)
    assert _is_open(_required_top_item(workspace_session))


def test_adding_a_video_item_opens_an_offline_viewer(workspace_session: WorkspaceSession, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([VideoItem(label="clip.mp4", video=video)])

        viewer = workspace_session.focused_offline_viewer()

        assert isinstance(viewer, DummyViewer)
    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._content_browser._tree.topLevelItemCount() == 1
    assert _is_open(_required_top_item(workspace_session))


def test_dropped_video_and_overlay_open_with_matching_handler(
    workspace_session: WorkspaceSession, tmp_path: Path
) -> None:
    """A dropped video with an overlay only one decoder reads opens directly."""
    video = tmp_path / "gate.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "gate.txt"
    overlay.write_text("1,1,0,0,1,1,1,-1,-1,-1\n")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.open_files([overlay, video])

    [content] = _store(workspace_session).contents()
    assert isinstance(content, SeekableVideoContent)
    assert content.source_spec.path == video
    assert [o.source_spec for o in content.overlays] == [FileOverlaySourceSpec(path=overlay, handler_type="MOT_FILE")]


def test_dropped_overlay_read_by_several_decoders_asks_for_the_handler(
    workspace_session: WorkspaceSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambiguous drops ask in a real prefilled dialog; cancelling adds nothing and releases each dialog."""
    video = tmp_path / "gate.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "scene.jsonl"
    overlay.write_text("{}\n")
    asked: list[tuple[str, str]] = []
    destroyed: list[bool] = []
    original_exec = AddVideoDialog.exec

    def cancel(dialog: AddVideoDialog) -> int:
        asked.append((dialog._video_path_edit.text(), dialog._overlay_path_edit.text()))
        dialog.destroyed.connect(lambda: destroyed.append(True))
        QTimer.singleShot(0, dialog.reject)
        return original_exec(dialog)

    monkeypatch.setattr(AddVideoDialog, "exec", cancel)
    for _ in range(3):
        workspace_session.open_files([video, overlay])
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert asked == [(str(video), str(overlay))] * 3
    assert destroyed == [True] * 3
    assert workspace_session.widget().findChildren(AddVideoDialog) == []
    assert _store(workspace_session).workspace.items == ()


def test_adding_a_live_stream_item_opens_a_live_viewer(workspace_session: WorkspaceSession) -> None:
    with patch("ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget", DummyViewer):
        workspace_session.add_items([LiveStreamItem(label="Camera", host="camera.local", username="root")])

        viewer = workspace_session._center_area.get_focused_widget()

        assert isinstance(viewer, DummyViewer)
    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._content_browser._tree.topLevelItemCount() == 1
    assert _is_open(_required_top_item(workspace_session))


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
    assert workspace_session._center_area.get_widget_count() == 0
    [(parent, title, message)] = [call.args for call in warning.call_args_list]
    assert (parent, title) == (workspace_session.widget(), "Open Content")
    assert message.startswith("Clip: ")
    assert workspace_session._content_browser._tree.topLevelItemCount() == 1
    row = cast(WorkspaceBrowserRow, _required_top_item(workspace_session).data(TREE_LABEL_COLUMN, TREE_ROW_ROLE))
    assert (row.label, row.unavailable_reason) == ("Clip", message.removeprefix("Clip: "))


def test_adding_live_content_auto_opens_live_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    content = _make_live_video("live")

    with patch("ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(content)])

    assert workspace_session._center_area.get_widget_count() == 1
    assert _is_open(_required_top_item(workspace_session))


def test_adding_playlist_auto_expands_and_marks_open_entry(
    workspace_session: WorkspaceSession,
) -> None:
    playlist = _make_playlist()

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(playlist)])

    top_item = _required_top_item(workspace_session)
    assert top_item.isExpanded()
    assert _is_open(_required_child(top_item, 0))


def test_viewer_current_item_change_updates_browser_open_indicator(
    workspace_session: WorkspaceSession,
) -> None:
    playlist = _make_playlist()

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(playlist)])

    viewer = cast(DummyViewer, workspace_session._center_area.get_focused_widget())
    top_item = _required_top_item(workspace_session)
    assert _is_open(_required_child(top_item, 0))
    assert not _is_open(_required_child(top_item, 1))

    viewer.start_index = 1
    viewer.on_screen_item_changed.emit(viewer.current_on_screen_item())

    top_item = _required_top_item(workspace_session)
    assert not _is_open(_required_child(top_item, 0))
    assert _is_open(_required_child(top_item, 1))


def test_focusing_open_viewer_preserves_open_indicators(
    workspace_session: WorkspaceSession,
) -> None:
    first = _make_video("first.mp4")
    second = _make_video("second.mp4")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(first)])
        first_widget = workspace_session._center_area.get_focused_widget()
        assert first_widget is not None
        first_widget.set_pinned(True)

        workspace_session.add_items([content_item(second)])

    assert _is_open(_required_top_item(workspace_session, 0))
    assert _is_open(_required_top_item(workspace_session, 1))

    workspace_session._center_area._set_focused_widget(first_widget)

    assert _is_open(_required_top_item(workspace_session, 0))
    assert _is_open(_required_top_item(workspace_session, 1))


def test_open_content_failure_shows_error_and_keeps_existing_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    existing = _make_video("existing")
    broken = _make_video("broken")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(existing)])

    assert workspace_session._center_area.get_widget_count() == 1

    with (
        patch(
            "ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget",
            side_effect=RuntimeError("Failed to load local media: boom"),
        ),
        patch("PySide6.QtWidgets.QMessageBox.warning") as warning,
    ):
        workspace_session._workspace_controller._open_content(broken)

    assert workspace_session._center_area.get_widget_count() == 1
    warning.assert_called_once_with(workspace_session.widget(), "Open Content", "Failed to load local media: boom")


def test_deferred_open_failure_restores_existing_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    existing = _make_video("existing")
    broken = _make_video("broken")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(existing)])

    existing_widget = cast(DummyViewer, workspace_session._center_area.get_focused_widget())
    assert existing_widget is not None

    with (
        patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _FailingAttachViewer),
        patch("PySide6.QtWidgets.QMessageBox.warning") as warning,
    ):
        workspace_session._workspace_controller._open_content(broken)

    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._center_area.get_focused_widget() is existing_widget
    assert not existing_widget.cleaned_up
    assert _is_open(_required_top_item(workspace_session, 0))
    warning.assert_called_once_with(workspace_session.widget(), "Open Content", "Failed to load local media: boom")


def test_replaced_viewer_is_unregistered_before_deferred_deletion(
    workspace_session: WorkspaceSession,
) -> None:
    first = _make_video("first.mp4")
    second = _make_video("second.mp4")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(first)])
        first_widget = cast(DummyViewer, workspace_session._center_area.get_focused_widget())

        workspace_session.add_items([content_item(second)])
        second_widget = cast(DummyViewer, workspace_session._center_area.get_focused_widget())

    item_ref = ConsiderationItemRef.playlist_entry(second.content_id, 0)
    workspace_session._workspace_controller._on_item_consideration_changed(item_ref, False)

    assert first_widget.cleaned_up
    assert first_widget.refresh_calls == []
    assert second_widget.refresh_calls == [(item_ref, False)]


def test_open_to_side_keeps_preview_viewer_and_next_open_replaces_only_the_preview(
    workspace_session: WorkspaceSession,
) -> None:
    first = _make_video("first.mp4")
    second = _make_video("second.mp4")
    browser = workspace_session._content_browser

    def shown_names() -> list[str]:
        return [
            widget.get_display_name()
            for leaf in workspace_session._center_area._list_leaves()
            if (widget := leaf.get_widget()) is not None
        ]

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(first)])
        workspace_session.add_items([content_item(second)])
        assert shown_names() == ["second.mp4"]

        browser.content_open_to_side_requested.emit(first, 0)
        assert shown_names() == ["second.mp4", "first.mp4"]

        browser.content_activated.emit(first, 0)

    assert shown_names() == ["first.mp4", "first.mp4"]
    assert len(workspace_session._workspace_controller._widget_item_ids) == 2


def test_sidebar_appears_with_content_at_its_width_and_toggles(workspace_session: WorkspaceSession) -> None:
    """The sidebar stays hidden on the welcome screen, appears at its width with content, and the user can hide it."""
    window = workspace_session.widget()
    window.resize(1200, 700)
    QCoreApplication.processEvents()
    splitter = window._splitter
    assert not window.is_sidebar_shown()

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(_make_video("a"))])
    QCoreApplication.processEvents()
    assert window.is_sidebar_shown()
    width = splitter.sizes()[0]
    assert 0 < width < 1200 // 2

    window.toggle_sidebar()
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()

    # Hidden by the user, it stays hidden when content changes.
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
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
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(_make_video("c"))])
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()
    window.toggle_sidebar()
    assert window.is_sidebar_shown() and splitter.sizes()[0] == width + 60


@pytest.mark.usefixtures("restore_app_appearance")
def test_sidebar_default_width_follows_text_size_until_dragged(workspace_session: WorkspaceSession) -> None:
    """An undragged sidebar keeps its default width in characters when the text size changes; a dragged one stays."""
    from ax_devil.modules.chrome.theme import apply_text_size

    window = workspace_session.widget()
    window.resize(1200, 700)
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        workspace_session.add_items([content_item(_make_video("a"))])
    QCoreApplication.processEvents()
    splitter = window._splitter
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
