from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem
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
    OverlayContent,
    OverlayFile,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    SeekableVideoContent,
    VideoItem,
    WorkspaceItem,
)
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.content_browser import TREE_LABEL_COLUMN, TREE_ROW_ROLE, ContentBrowserWidget
from ax_devil.modules.workspace.ui.recent_videos import RecentVideos
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.split_view import LeafContainer, SplitView
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.widgets import required_child
from tests.helpers.workspace import DummyViewer, FakeResolutionContext, content_item

_OFFLINE_VIEWER = "ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget"
_LIVE_VIEWER = "ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget"


def _center(session: WorkspaceSession) -> SplitView:
    center = session.widget().findChild(SplitView)
    assert center is not None
    return center


def _browser(session: WorkspaceSession) -> ContentBrowserWidget:
    browser = session.widget().findChild(ContentBrowserWidget)
    assert browser is not None
    return browser


def _tree(session: WorkspaceSession) -> QTreeWidget:
    tree: QTreeWidget | None = _browser(session).findChild(QTreeWidget)
    assert tree is not None
    return tree


def _required_top_item(session: WorkspaceSession, index: int = 0) -> QTreeWidgetItem:
    item = _tree(session).topLevelItem(index)
    assert item is not None
    return item


def _is_open(item: QTreeWidgetItem) -> bool:
    return cast(WorkspaceBrowserRow, item.data(TREE_LABEL_COLUMN, TREE_ROW_ROLE)).is_open is True


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


@pytest.fixture()
def recent_path(tmp_path: Path) -> Path:
    return tmp_path / "recent-videos.json"


@pytest.fixture()
def workspace_session(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, recent_path: Path
) -> WorkspaceSession:
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        recent_videos=RecentVideos(recent_path),
    )
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
        browser.content_open_to_side_requested.emit(content, 0)
        assert _center(workspace_session).get_widget_count() == 2

        browser.content_remove_requested.emit(content)

    assert _center(workspace_session).get_widget_count() == 0
    assert _store(workspace_session).workspace.items == ()
    assert _tree(workspace_session).topLevelItemCount() == 0


class _TwoPlaylists:
    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        return [_make_playlist("train"), _make_playlist("test")]


def test_removing_one_content_of_an_item_removes_the_whole_item(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        context=FakeResolutionContext(resolvers={"runs": _TwoPlaylists()}),
        recent_videos=RecentVideos(tmp_path / "recent-videos.json"),
    )
    qtbot.addWidget(session.widget())
    kept = content_item(_make_video("kept.mp4"))

    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([PlaylistItem(label="Runs", resolver="runs"), kept])
        assert _tree(session).topLevelItemCount() == 3
        _browser(session).content_remove_requested.emit(_store(session).contents()[1])

    assert _store(session).workspace.items == (kept,)
    assert _tree(session).topLevelItemCount() == 1


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
    video = tmp_path / "startup.mp4"
    video.write_bytes(b"")
    items: dict[str, WorkspaceItem] = {
        "video": VideoItem(label="startup.mp4", video=video),
        "live_stream": LiveStreamItem(label="Camera", host="camera.local", username="root"),
    }

    with patch(_OFFLINE_VIEWER, DummyViewer), patch(_LIVE_VIEWER, DummyViewer):
        workspace_session.add_items([items[kind]])

    assert _center(workspace_session).get_widget_count() == 1
    assert _tree(workspace_session).topLevelItemCount() == 1
    assert _is_open(_required_top_item(workspace_session))


def test_dropped_video_and_overlay_open_with_matching_handler_and_join_recent_videos(
    workspace_session: WorkspaceSession, tmp_path: Path, recent_path: Path
) -> None:
    """A dropped video with an overlay only one decoder reads opens directly and appears under Recent."""
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
    [recent] = RecentVideos(recent_path).entries()
    assert (recent.video, recent.overlays) == (video, (OverlayFile(overlay, "MOT_FILE"),))


def test_dropped_overlay_read_by_several_decoders_asks_for_the_handler(
    workspace_session: WorkspaceSession, tmp_path: Path, recent_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert RecentVideos(recent_path).entries() == ()


@pytest.mark.parametrize("kind", ["video", "live_stream"])
def test_an_item_that_cannot_open_stays_in_the_workspace_and_the_user_is_told(
    workspace_session: WorkspaceSession, tmp_path: Path, kind: str
) -> None:
    video = tmp_path / "startup.mp4"
    overlay = tmp_path / "startup.json"
    video.write_bytes(b"")
    overlay.write_text("{}")
    items: dict[str, WorkspaceItem] = {
        "video": VideoItem(label="Startup", video=video, overlays=(OverlayFile(overlay, "MISSING"),)),
        "live_stream": LiveStreamItem(label="Startup", host="camera.local", overlay_mode=LiveOverlayMode.RTSP),
    }

    with patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
        workspace_session.add_items([items[kind]])

    assert _store(workspace_session).workspace.items == (items[kind],)
    assert _center(workspace_session).get_widget_count() == 0
    assert _tree(workspace_session).topLevelItemCount() == 0
    [(parent, title, message)] = [call.args for call in warning.call_args_list]
    assert (parent, title) == (workspace_session.widget(), "Open Content")
    assert message.startswith("Startup: ")


def test_adding_live_content_auto_opens_live_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    content = _make_live_video("live")

    with patch(_LIVE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(content)])

    assert _focused(workspace_session).payload is _store(workspace_session).contents()[0]
    assert _is_open(_required_top_item(workspace_session))


def test_viewer_current_item_change_updates_browser_open_indicator(
    workspace_session: WorkspaceSession,
) -> None:
    playlist = _make_playlist()

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(playlist)])

    viewer = _focused(workspace_session)
    top_item = _required_top_item(workspace_session)
    assert top_item.isExpanded()
    assert _is_open(required_child(top_item, 0))
    assert not _is_open(required_child(top_item, 1))

    viewer.start_index = 1
    viewer.on_screen_item_changed.emit(viewer.current_on_screen_item())

    top_item = _required_top_item(workspace_session)
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

    assert _is_open(_required_top_item(workspace_session, 0))
    assert _is_open(_required_top_item(workspace_session, 1))

    QTest.mouseClick(first_widget, Qt.MouseButton.LeftButton)

    assert workspace_session.focused_widget() is first_widget
    assert _is_open(_required_top_item(workspace_session, 0))
    assert _is_open(_required_top_item(workspace_session, 1))


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
    assert _is_open(_required_top_item(workspace_session, 0))
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
    _browser(workspace_session).item_consideration_change_requested.emit(item_ref, False)

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
        browser.content_open_to_side_requested.emit(stored_first, 0)
        assert shown_names() == ["first.mp4", "second.mp4"]

        browser.content_activated.emit(stored_first, 0)

    assert shown_names() == ["first.mp4", "first.mp4"]
    browser.content_remove_requested.emit(stored_first)
    assert _center(workspace_session).get_widget_count() == 0


def _sidebar_splitter(workspace_session: WorkspaceSession) -> QSplitter:
    splitter: QSplitter | None = workspace_session.widget().findChild(QSplitter, "WorkspaceSidebarSplitter")
    assert splitter is not None
    return splitter


def test_sidebar_appears_with_content_at_its_width_and_toggles(workspace_session: WorkspaceSession) -> None:
    """The sidebar stays hidden on the welcome screen, appears at its width with content, and the user can hide it."""
    window = workspace_session.widget()
    window.resize(1200, 700)
    QCoreApplication.processEvents()
    splitter = _sidebar_splitter(workspace_session)
    assert not window.is_sidebar_shown()

    with patch(_OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(_make_video("a"))])
    QCoreApplication.processEvents()
    assert window.is_sidebar_shown()
    width = splitter.sizes()[0]
    assert 0 < width < 1200 // 2

    window.toggle_sidebar()
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()

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
