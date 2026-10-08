from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QTreeWidgetItem, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveOverlayMode,
    LiveRTSPStreamSpec,
    LiveStreamStartup,
    LiveVideoContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
    VideoFileStartup,
    WorkspaceManager,
)
from ax_devil.modules.workspace.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.recent_videos import RecentVideos
from ax_devil.modules.workspace.session import WorkspaceSession
from ax_devil.modules.workspace.viewer_host import WorkspaceWidget


def _required_top_item(workspace_session: WorkspaceSession, index: int = 0) -> QTreeWidgetItem:
    item = workspace_session._content_browser._tree.topLevelItem(index)
    assert item is not None
    return item


def _required_child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    child = item.child(index)
    assert child is not None
    return child


class _DummyViewer(WorkspaceWidget):
    """Minimal viewer used to isolate workspace-session behavior."""

    def __init__(
        self,
        payload: object,
        render_catalog_manager: SceneRenderCatalogManager,
        start_index: int = 0,
        parent: QWidget | None = None,
        consideration_query: WorkspaceManager | None = None,
    ) -> None:
        self._payload = payload
        self._start_index = start_index
        self._consideration_query = consideration_query
        self._render_catalog_manager = render_catalog_manager
        self.cleaned_up = False
        self.refresh_calls: list[tuple[object, bool]] = []
        super().__init__(parent)

    def _setup_widget_ui(self) -> None:
        pass

    def get_display_name(self) -> str:
        return getattr(self._payload, "display_name", "dummy")

    def current_on_screen_item(self) -> OnScreenWorkspaceItem | None:
        if isinstance(self._payload, (SeekableVideoContent, LiveVideoContent)):
            return OnScreenWorkspaceItem(kind="video", content_id=self._payload.content_id)
        if isinstance(self._payload, PlaylistContent):
            return OnScreenWorkspaceItem(
                kind="playlist_entry",
                content_id=self._payload.content_id,
                entry_index=self._start_index,
            )
        return None

    def cleanup(self) -> None:
        self.cleaned_up = True

    def refresh_item_consideration(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        self.refresh_calls.append((item_ref, considered))


class _FailingAttachViewer(_DummyViewer):
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


@pytest.fixture()
def workspace_session(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> WorkspaceSession:
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        recent_videos=RecentVideos(tmp_path / "recent-videos.json"),
    )
    widget = session.widget()
    qtbot.addWidget(widget)
    widget.show()
    return session


def test_workspace_clear_removes_existing_viewers(
    workspace_session: WorkspaceSession,
) -> None:
    assert not workspace_session._content_browser.isVisible()
    content = _make_video("existing")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(content)
        assert workspace_session._center_area.get_widget_count() == 1
        assert workspace_session._content_browser.isVisible()

        workspace_session.clear()

        assert workspace_session._center_area.get_widget_count() == 0
        assert workspace_session._content_browser._tree.topLevelItemCount() == 0
        assert not workspace_session._content_browser.isVisible()


def test_removing_content_closes_all_open_viewers_for_same_content(
    workspace_session: WorkspaceSession,
) -> None:
    content = _make_video("duplicate")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(content)
        first_viewer = workspace_session._center_area.get_focused_widget()
        assert first_viewer is not None
        first_viewer.set_pinned(True)

        workspace_session.add_content(content)
        assert workspace_session._center_area.get_widget_count() == 2
        assert first_viewer.is_pinned()

        workspace_session.remove_content(content)

        assert workspace_session._center_area.get_widget_count() == 0


def test_removing_video_closes_standalone_and_playlist_viewers_that_depend_on_it(
    workspace_session: WorkspaceSession,
) -> None:
    video = _make_video("shared.mp4")
    playlist = PlaylistContent(
        display_name="Playlist",
        entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
    )

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(playlist)
        playlist_viewer = workspace_session._center_area.get_focused_widget()
        assert playlist_viewer is not None
        playlist_viewer.set_pinned(True)
        workspace_session.add_content(video)

        assert workspace_session._center_area.get_widget_count() == 2
        workspace_session.remove_content(video)

    assert workspace_session._center_area.get_widget_count() == 0


def test_removing_playlist_leaves_standalone_underlying_video_viewer_open(
    workspace_session: WorkspaceSession,
) -> None:
    video = _make_video("shared.mp4")
    playlist = PlaylistContent(
        display_name="Playlist",
        entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
    )

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(video)
        video_viewer = workspace_session._center_area.get_focused_widget()
        assert video_viewer is not None
        video_viewer.set_pinned(True)
        workspace_session.add_content(playlist)

        assert workspace_session._center_area.get_widget_count() == 2
        workspace_session.remove_content(playlist)

    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._center_area.get_focused_widget() is video_viewer


def test_adding_content_auto_opens_viewers(
    workspace_session: WorkspaceSession,
) -> None:
    content = _make_video("existing")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(content)

    assert workspace_session._center_area.get_widget_count() == 1
    viewer = workspace_session._center_area.get_focused_widget()
    assert isinstance(viewer, _DummyViewer)
    assert viewer._consideration_query is workspace_session._workspace_manager
    assert _required_top_item(workspace_session).font(0).bold()


def test_loading_video_file_startup_opens_offline_viewer_through_workspace_path(
    workspace_session: WorkspaceSession,
) -> None:
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.load_startup_content(VideoFileStartup(video_path=Path("/tmp/startup.mp4")))

        viewer = workspace_session.focused_offline_viewer()

        assert isinstance(viewer, _DummyViewer)
    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._content_browser._tree.topLevelItemCount() == 1
    assert _required_top_item(workspace_session).font(0).bold()


def test_dropped_video_and_overlay_open_with_matching_handler_and_join_recent_videos(
    workspace_session: WorkspaceSession, tmp_path: Path
) -> None:
    """A dropped video with an overlay only one decoder reads opens directly and appears under Recent."""
    video = tmp_path / "gate.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "gate.txt"
    overlay.write_text("1,1,0,0,1,1,1,-1,-1,-1\n")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.open_files([overlay, video])

    [content] = workspace_session._workspace_manager.get_contents()
    assert isinstance(content, SeekableVideoContent)
    assert content.source_spec.path == video
    assert [o.source_spec for o in content.overlays] == [FileOverlaySourceSpec(path=overlay, handler_type="MOT_FILE")]
    expected = VideoFileStartup(video_path=video, overlay_path=overlay, handler_type="MOT_FILE")
    assert workspace_session._welcome._recent_videos == (expected,)


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
    assert workspace_session._workspace_manager.get_contents() == []
    assert workspace_session._welcome._recent_videos == ()


def test_loading_live_stream_startup_opens_live_viewer_through_workspace_path(
    workspace_session: WorkspaceSession,
) -> None:
    with patch("ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget", _DummyViewer):
        workspace_session.load_startup_content(LiveStreamStartup(host="camera.local", username="root", password="pass"))

        viewer = workspace_session._center_area.get_focused_widget()

        assert isinstance(viewer, _DummyViewer)
    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._content_browser._tree.topLevelItemCount() == 1
    assert _required_top_item(workspace_session).font(0).bold()


def test_loading_video_file_startup_with_overlay_without_handler_leaves_workspace_unchanged(
    workspace_session: WorkspaceSession,
) -> None:
    workspace_session.load_startup_content(
        VideoFileStartup(video_path=Path("/tmp/startup.mp4"), overlay_path=Path("/tmp/startup.json")),
    )

    assert workspace_session._center_area.get_widget_count() == 0
    assert workspace_session._content_browser._tree.topLevelItemCount() == 0


def test_loading_live_stream_startup_with_rtsp_overlay_without_handler_leaves_workspace_unchanged(
    workspace_session: WorkspaceSession,
) -> None:
    workspace_session.load_startup_content(
        LiveStreamStartup(
            host="camera.local",
            username="root",
            password="pass",
            overlay_mode=LiveOverlayMode.RTSP,
        ),
    )

    assert workspace_session._center_area.get_widget_count() == 0
    assert workspace_session._content_browser._tree.topLevelItemCount() == 0


def test_adding_live_content_auto_opens_live_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    content = _make_live_video("live")

    with patch("ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(content)

    assert workspace_session._center_area.get_widget_count() == 1
    assert _required_top_item(workspace_session).font(0).bold()


def test_adding_playlist_auto_expands_and_marks_open_entry(
    workspace_session: WorkspaceSession,
) -> None:
    playlist = _make_playlist()

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(playlist)

    top_item = _required_top_item(workspace_session)
    assert top_item.isExpanded()
    assert _required_child(top_item, 0).font(0).bold()


def test_viewer_current_item_change_updates_browser_open_indicator(
    workspace_session: WorkspaceSession,
) -> None:
    playlist = _make_playlist()

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(playlist)

    viewer = cast(_DummyViewer, workspace_session._center_area.get_focused_widget())
    top_item = _required_top_item(workspace_session)
    assert _required_child(top_item, 0).font(0).bold()
    assert not _required_child(top_item, 1).font(0).bold()

    viewer._start_index = 1
    viewer.on_screen_item_changed.emit(viewer.current_on_screen_item())

    top_item = _required_top_item(workspace_session)
    assert not _required_child(top_item, 0).font(0).bold()
    assert _required_child(top_item, 1).font(0).bold()


def test_focusing_open_viewer_preserves_open_indicators(
    workspace_session: WorkspaceSession,
) -> None:
    first = _make_video("first.mp4")
    second = _make_video("second.mp4")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(first)
        first_widget = workspace_session._center_area.get_focused_widget()
        assert first_widget is not None
        first_widget.set_pinned(True)

        workspace_session.add_content(second)

    assert _required_top_item(workspace_session, 0).font(0).bold()
    assert _required_top_item(workspace_session, 1).font(0).bold()

    workspace_session._center_area._set_focused_widget(first_widget)

    assert _required_top_item(workspace_session, 0).font(0).bold()
    assert _required_top_item(workspace_session, 1).font(0).bold()


def test_open_content_failure_shows_error_and_keeps_existing_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    existing = _make_video("existing")
    broken = _make_video("broken")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(existing)

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

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(existing)

    existing_widget = cast(_DummyViewer, workspace_session._center_area.get_focused_widget())
    assert existing_widget is not None

    with (
        patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _FailingAttachViewer),
        patch("PySide6.QtWidgets.QMessageBox.warning") as warning,
    ):
        workspace_session._workspace_controller._open_content(broken)

    assert workspace_session._center_area.get_widget_count() == 1
    assert workspace_session._center_area.get_focused_widget() is existing_widget
    assert not existing_widget.cleaned_up
    assert _required_top_item(workspace_session, 0).font(0).bold()
    warning.assert_called_once_with(workspace_session.widget(), "Open Content", "Failed to load local media: boom")


def test_replaced_viewer_is_unregistered_before_deferred_deletion(
    workspace_session: WorkspaceSession,
) -> None:
    first = _make_video("first.mp4")
    second = _make_video("second.mp4")

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(first)
        first_widget = cast(_DummyViewer, workspace_session._center_area.get_focused_widget())

        workspace_session.add_content(second)
        second_widget = cast(_DummyViewer, workspace_session._center_area.get_focused_widget())

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

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(first)
        workspace_session.add_content(second)
        assert shown_names() == ["second.mp4"]

        browser.content_open_to_side_requested.emit(first, 0)
        assert shown_names() == ["second.mp4", "first.mp4"]

        browser.content_activated.emit(first, 0)

    assert shown_names() == ["first.mp4", "first.mp4"]
    assert len(workspace_session._workspace_controller._widget_content_ids) == 2


def test_sidebar_appears_with_content_at_its_width_and_toggles(workspace_session: WorkspaceSession) -> None:
    """The sidebar stays hidden on the welcome screen, appears at its width with content, and the user can hide it."""
    window = workspace_session.widget()
    window.resize(1200, 700)
    QCoreApplication.processEvents()
    splitter = window._splitter
    assert not window.is_sidebar_shown()

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(_make_video("a"))
    QCoreApplication.processEvents()
    assert window.is_sidebar_shown()
    width = splitter.sizes()[0]
    assert 0 < width < 1200 // 2

    window.toggle_sidebar()
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()

    # Hidden by the user, it stays hidden when content changes.
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(_make_video("b"))
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
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", _DummyViewer):
        workspace_session.add_content(_make_video("c"))
    QCoreApplication.processEvents()
    assert not window.is_sidebar_shown()
    window.toggle_sidebar()
    assert window.is_sidebar_shown() and splitter.sizes()[0] == width + 60
