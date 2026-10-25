"""What the sidebar rows do for an item in the workspace session: name edits, add, export, information, and connect."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.workspace.core import ItemResolution, LiveStreamItem, Workspace
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.browser_rows import RowAction
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.live_stream_status import LiveStreamState, LiveStreamStatus
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.workspace import DummyViewer
from tests.helpers.workspace_session import (
    LIVE_VIEWER,
    OFFLINE_VIEWER,
    ask_row_action,
    center_of,
    focused_viewer,
    item_is_open,
    item_rows,
    required_item_row,
    row_of,
    store_of,
    tree_of,
    video_file_item,
)


def test_editing_an_items_name_updates_its_row_and_open_viewer(
    workspace_session: WorkspaceSession, tmp_path: Path
) -> None:
    clip = video_file_item(tmp_path, "lot.mp4")
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([clip])
    viewer = focused_viewer(workspace_session)

    store_of(workspace_session).edit_item(replace(clip, label="North gate"))

    assert viewer._title_label.text() == "North gate"
    assert row_of(required_item_row(workspace_session)).label == "North gate"
    assert store_of(workspace_session).is_modified

    store_of(workspace_session).edit_item(replace(clip, label=""))

    assert viewer._title_label.text() == "lot.mp4"
    assert row_of(required_item_row(workspace_session)).label == "lot.mp4"


def test_adding_the_same_camera_twice_starts_two_streams_whose_rows_differ_by_detail_and_status(
    workspace_session: WorkspaceSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AddLiveStreamDialog, "exec", lambda dialog: AddLiveStreamDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        AddLiveStreamDialog,
        "get_result",
        lambda dialog: ItemResolution.of(
            LiveStreamItem(host="192.0.2.10"), default_resolution_context(), CancelToken()
        ),
    )

    with patch(LIVE_VIEWER, DummyViewer):
        workspace_session.add_live_stream()
        workspace_session.add_live_stream()

    first, second = store_of(workspace_session).workspace.items
    assert (first.label, second.label) == ("", ""), "no generated name is stored"
    viewers = {
        shown.content_id: viewer
        for viewer in center_of(workspace_session).findChildren(DummyViewer)
        if (shown := viewer.current_on_screen_item()) is not None
    }
    assert len(viewers) == 2, "the second stream did not replace the first"
    assert len({row_of(item).detail for item in item_rows(workspace_session)}) == 2

    viewers[store_of(workspace_session).resolution(second.id).contents[0].content_id].set_stream(
        LiveStreamStatus(LiveStreamState.RECONNECTING, attempts=2)
    )

    details = [row_of(item).detail for item in item_rows(workspace_session)]
    assert details[0] != details[1] and "Reconnecting" not in details[0]
    assert details[1].endswith("Reconnecting (2)")

    with patch(LIVE_VIEWER, DummyViewer):  # The edit replaces the open viewer.
        store_of(workspace_session).edit_item(LiveStreamItem(host="192.0.2.20", id=second.id))

    assert [row_of(item).label for item in item_rows(workspace_session)] == ["192.0.2.10", "192.0.2.20"]


def test_export_waits_for_the_row_to_be_open(workspace_session: WorkspaceSession, tmp_path: Path) -> None:
    first, second = video_file_item(tmp_path, "first.mp4"), video_file_item(tmp_path, "second.mp4")
    with patch(OFFLINE_VIEWER, DummyViewer), patch.object(DummyViewer, "export_video") as export:
        workspace_session.add_items([first])
        workspace_session.add_items([second])  # Opens in the preview pane in place of the first.
        ask_row_action(workspace_session, first.id, RowAction.EXPORT_VIDEO)
        export.assert_not_called()

        ask_row_action(workspace_session, second.id, RowAction.EXPORT_VIDEO)
        export.assert_called_once_with()


def test_information_on_a_row_shows_the_information_of_that_item(
    workspace_session: WorkspaceSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = video_file_item(tmp_path, "first.mp4"), video_file_item(tmp_path, "second.mp4")
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([first, second])
    shown: list[str] = []

    def record(dialog: WorkspaceItemInfoDialog) -> int:
        shown.append(dialog.windowTitle())
        return WorkspaceItemInfoDialog.DialogCode.Accepted

    monkeypatch.setattr(WorkspaceItemInfoDialog, "exec", record)

    ask_row_action(workspace_session, second.id, RowAction.INFORMATION)

    assert shown == ["second.mp4"]


def test_choosing_open_from_a_menu_whose_item_was_removed_meanwhile_opens_nothing(
    workspace_session: WorkspaceSession, tmp_path: Path
) -> None:
    clip = video_file_item(tmp_path, "lot.mp4")
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([clip])
        tree = tree_of(workspace_session)
        position = tree.visualItemRect(required_item_row(workspace_session)).center()

        def remove_then_choose() -> None:
            menu = tree.findChildren(QMenu)[-1]
            menu.close()
            store_of(workspace_session).remove_item(clip.id)
            next(entry for entry in menu.actions() if entry.text() == "Open").trigger()

        QTimer.singleShot(0, remove_then_choose)
        tree.customContextMenuRequested.emit(position)

    assert store_of(workspace_session).workspace.items == ()
    assert center_of(workspace_session).get_widget_count() == 0


def _press_row_action(session: WorkspaceSession, label: str) -> None:
    """Press the action button of the Workspace view's top-level row named *label*."""
    [item] = [item for item in item_rows(session) if row_of(item).label == label]
    widget = tree_of(session).itemWidget(item, 0)
    assert widget is not None
    [button] = widget.findChildren(SmallButton)
    button.click()


def test_connect_and_disconnect_in_the_workspace_view_open_and_close_the_camera(
    workspace_session: WorkspaceSession,
) -> None:
    store_of(workspace_session).replace_workspace(Workspace(items=(LiveStreamItem(label="Gate", host="camera.local"),)))
    with patch(LIVE_VIEWER, DummyViewer):
        _press_row_action(workspace_session, "Gate")
        assert center_of(workspace_session).get_widget_count() == 1
        assert item_is_open(required_item_row(workspace_session))

        _press_row_action(workspace_session, "Gate")

    assert center_of(workspace_session).get_widget_count() == 0
    assert not item_is_open(required_item_row(workspace_session))
