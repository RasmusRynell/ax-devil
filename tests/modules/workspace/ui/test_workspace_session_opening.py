"""The workspace session opens added and dropped content in viewers and reports what cannot open."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    LiveOverlayMode,
    LiveRTSPStreamSpec,
    LiveStreamItem,
    LiveVideoContent,
    OverlayFile,
    SeekableVideoContent,
    VideoItem,
    WorkspaceItem,
)
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.widgets import required_child
from tests.helpers.workspace import DummyViewer, content_item
from tests.helpers.workspace_session import (
    LIVE_VIEWER,
    OFFLINE_VIEWER,
    center_of,
    focused_viewer,
    item_is_open,
    item_rows,
    playlist_content,
    required_item_row,
    row_of,
    store_of,
    tree_of,
    video_content,
    video_file_item,
)


class _FailingAttachViewer(DummyViewer):
    """Viewer test double that fails during deferred workspace startup."""

    def on_workspace_attached(self) -> None:
        """Raise a deferred startup failure."""
        raise RuntimeError("Failed to load local media: boom")


def _make_live_video(name: str) -> LiveVideoContent:
    return LiveVideoContent(
        display_name=name,
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass"),
        overlays=(),
    )


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

    with patch(OFFLINE_VIEWER, DummyViewer), patch(LIVE_VIEWER, DummyViewer):
        workspace_session.add_items([items[kind]])

    assert center_of(workspace_session).get_widget_count() == 1
    assert len(item_rows(workspace_session)) == 1
    assert item_is_open(required_item_row(workspace_session))


def test_dropped_video_and_overlay_open_with_matching_handler(
    workspace_session: WorkspaceSession, tmp_path: Path
) -> None:
    """A dropped video with an overlay only one decoder reads opens directly."""
    video = tmp_path / "gate.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "gate.txt"
    overlay.write_text("1,1,0,0,1,1,1,-1,-1,-1\n")

    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.open_files([overlay, video])

    content = focused_viewer(workspace_session).payload
    assert isinstance(content, SeekableVideoContent)
    assert content.source_spec.path == video
    assert [o.source_spec for o in content.overlays] == [FileOverlaySourceSpec(path=overlay, handler_type="MOT_FILE")]


def test_dropped_videos_are_added_in_drop_order_and_the_first_opens(
    workspace_session: WorkspaceSession, tmp_path: Path
) -> None:
    first, second = video_file_item(tmp_path, "first.mp4"), video_file_item(tmp_path, "second.mp4")

    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.open_files([first.video, second.video])

    assert [item.video for item in cast(tuple[VideoItem, ...], store_of(workspace_session).workspace.items)] == [
        first.video,
        second.video,
    ]
    assert focused_viewer(workspace_session).get_display_name() == "first.mp4"


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
    assert store_of(workspace_session).workspace.items == ()
    assert tree_of(workspace_session).topLevelItemCount() == 0


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

    with patch("ax_devil.modules.workspace.ui.workspace_controller.notify") as warning:
        workspace_session.add_items([items[kind]])

    assert store_of(workspace_session).workspace.items == (items[kind],)
    assert center_of(workspace_session).get_widget_count() == 0
    [(parent, title, message)] = [call.args for call in warning.call_args_list]
    assert (parent, title) == (workspace_session.widget(), "Open Content")
    assert message.startswith("Clip: ")
    assert len(item_rows(workspace_session)) == 1
    row = row_of(required_item_row(workspace_session))
    assert (row.label, row.unavailable_reason) == ("Clip", message.removeprefix("Clip: "))

    with patch("ax_devil.modules.workspace.ui.workspace_controller.notify") as again:
        tree_of(workspace_session).itemDoubleClicked.emit(required_item_row(workspace_session), 0)

    assert [call.args[1:] for call in again.call_args_list] == [("Open Content", message)], "a double click says why"
    assert center_of(workspace_session).get_widget_count() == 0


def test_adding_live_content_auto_opens_live_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    content = _make_live_video("live")

    with patch(LIVE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(content)])

    assert focused_viewer(workspace_session).payload is store_of(workspace_session).contents()[0]
    assert item_is_open(required_item_row(workspace_session))


def test_viewer_current_item_change_updates_browser_open_indicator(
    workspace_session: WorkspaceSession,
) -> None:
    playlist = playlist_content()

    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(playlist)])

    viewer = focused_viewer(workspace_session)
    top_item = required_item_row(workspace_session)
    assert top_item.isExpanded()
    assert item_is_open(required_child(top_item, 0))
    assert not item_is_open(required_child(top_item, 1))

    viewer.start_index = 1
    viewer.on_screen_item_changed.emit(viewer.current_on_screen_item())

    top_item = required_item_row(workspace_session)
    assert not item_is_open(required_child(top_item, 0))
    assert item_is_open(required_child(top_item, 1))


def test_focusing_open_viewer_preserves_open_indicators(
    workspace_session: WorkspaceSession,
) -> None:
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video_content("first.mp4"))])
        first_widget = focused_viewer(workspace_session)
        first_widget.set_pinned(True)
        workspace_session.add_items([content_item(video_content("second.mp4"))])

    assert item_is_open(required_item_row(workspace_session, 0))
    assert item_is_open(required_item_row(workspace_session, 1))

    QTest.mouseClick(first_widget, Qt.MouseButton.LeftButton)

    assert workspace_session.focused_widget() is first_widget
    assert item_is_open(required_item_row(workspace_session, 0))
    assert item_is_open(required_item_row(workspace_session, 1))


@pytest.mark.parametrize("failure", ["construct", "attach"])
def test_open_failure_shows_error_and_keeps_existing_viewer(workspace_session: WorkspaceSession, failure: str) -> None:
    """A viewer that fails to construct or to start leaves the existing preview pane open and running."""
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(video_content("existing"))])
    existing = focused_viewer(workspace_session)

    broken_viewer = (
        patch(OFFLINE_VIEWER, side_effect=RuntimeError("Failed to load local media: boom"))
        if failure == "construct"
        else patch(OFFLINE_VIEWER, _FailingAttachViewer)
    )
    with broken_viewer, patch("ax_devil.modules.workspace.ui.open_viewers.notify") as warning:
        workspace_session.add_items([content_item(video_content("broken"))])

    assert center_of(workspace_session).get_widget_count() == 1
    assert workspace_session.focused_widget() is existing
    assert not existing.cleaned_up
    assert item_is_open(required_item_row(workspace_session, 0))
    warning.assert_called_once_with(workspace_session.widget(), "Open Content", "Failed to load local media: boom")


def test_replaced_viewer_stops_receiving_consideration_updates(
    workspace_session: WorkspaceSession,
) -> None:
    first = video_content("first.mp4")
    second = video_content("second.mp4", with_overlay=True)

    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([content_item(first)])
        first_widget = focused_viewer(workspace_session)
        workspace_session.add_items([content_item(second)])
        second_widget = focused_viewer(workspace_session)

    stored_second = store_of(workspace_session).contents()[1]
    item_ref = stored_second.lane_consideration_ref(0, 0)
    store_of(workspace_session).set_item_considered(item_ref, False)

    assert first_widget.cleaned_up
    assert first_widget.refresh_calls == []
    assert second_widget.refresh_calls == [(item_ref, False)]
