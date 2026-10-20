"""Sidebar edits preserve open pane locations and refresh only the affected media."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLineEdit

from ax_devil.modules.workspace.core import BookmarkPosition, LiveStreamItem, PlaylistContent, PlaylistEntry
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.split_panes import LeafContainer
from tests.helpers.contents import make_video
from tests.helpers.forms import form_field
from tests.helpers.workspace import DummyViewer, content_item
from tests.helpers.workspace_session import (
    LIVE_VIEWER,
    OFFLINE_VIEWER,
    center_of,
    focused_viewer,
    store_of,
    video_file_item,
)


def test_edit_reopens_matching_playlist_entry_in_same_pinned_pane_and_keeps_other_viewer(
    workspace_session: WorkspaceSession,
) -> None:
    entries = tuple(PlaylistEntry(make_video(name).standalone_lanes(), True) for name in ("A", "B", "C"))
    playlist = PlaylistContent(display_name="Set", entries=entries)
    item = content_item(playlist)
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([item])
        old = focused_viewer(workspace_session)
        old.start_index = 1
        assert isinstance(old.payload, PlaylistContent)
        old.position = BookmarkPosition(content=old.payload, entry_index=1, frame=12, time_s=0.4)
        old.set_pinned(True)
        pane = old.parentWidget()
        workspace_session.add_items([content_item(make_video("unrelated"))])
        unrelated = focused_viewer(workspace_session)
        edited = replace(item, contents=(replace(playlist, entries=(entries[2], entries[0], entries[1])),))

        store_of(workspace_session).edit_item(edited)

    assert old.cleaned_up and not unrelated.cleaned_up
    assert workspace_session.focused_widget() is unrelated
    assert isinstance(pane, LeafContainer)
    current = pane.get_widget()
    assert isinstance(current, DummyViewer) and current is not old
    assert current.is_pinned() and (current.start_index, current.start_frame) == (2, 12)
    assert center_of(workspace_session).get_widget_count() == 2

    store_of(workspace_session).edit_item(replace(edited, contents=(replace(playlist, entries=(entries[0],)),)))
    assert current.cleaned_up and not unrelated.cleaned_up
    assert center_of(workspace_session).get_widget_count() == 1


def test_edit_reconnects_open_live_item_and_keeps_closed_live_item_closed(workspace_session: WorkspaceSession) -> None:
    opened, closed = LiveStreamItem(host="before.local"), LiveStreamItem(host="closed.local")
    with patch(LIVE_VIEWER, DummyViewer):
        workspace_session.add_items([opened])
        old = focused_viewer(workspace_session)
        workspace_session.add_items([])
        store = store_of(workspace_session)
        store.add_items([closed], open_first=False)
        store.edit_item(replace(closed, host="still-closed.local"))
        assert center_of(workspace_session).get_widget_count() == 1 and not old.cleaned_up

        store.edit_item(replace(opened, host="after.local"))

    assert old.cleaned_up
    viewer = focused_viewer(workspace_session)
    assert viewer.payload.source_spec.host == "after.local"
    assert center_of(workspace_session).get_widget_count() == 1


@pytest.mark.parametrize("outcome", ["cancel", "invalid", "unchanged", "repair"])
def test_video_edit_commits_only_a_valid_changed_form(
    workspace_session: WorkspaceSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    item = video_file_item(tmp_path, "before.mp4")
    replacement = video_file_item(tmp_path, "after.mp4")
    original_exec = AddVideoDialog.exec

    def submit(dialog: AddVideoDialog) -> int:
        def act() -> None:
            if outcome == "cancel":
                dialog.reject()
            elif outcome == "invalid":
                form_field(dialog, "Video File", QLineEdit).setText(str(tmp_path / "missing.mp4"))
                dialog.accept()
                assert dialog.get_result() is None
                dialog.reject()
            else:
                if outcome == "repair":
                    form_field(dialog, "Video File", QLineEdit).setText(str(replacement.video))
                dialog.accept()

        QTimer.singleShot(0, act)
        return int(original_exec(dialog))

    monkeypatch.setattr(AddVideoDialog, "exec", submit)
    with patch(OFFLINE_VIEWER, DummyViewer):
        workspace_session.add_items([item])
        old = focused_viewer(workspace_session)
        store = store_of(workspace_session)
        store.save_workspace(tmp_path / "work.ax-devil.workspace")
        before = store.workspace
        workspace_session.edit_item(item.id)

    if outcome == "repair":
        assert store.workspace.items == (replace(item, video=replacement.video),)
        assert store.is_modified and old.cleaned_up
    else:
        assert store.workspace is before and not store.is_modified and not old.cleaned_up
