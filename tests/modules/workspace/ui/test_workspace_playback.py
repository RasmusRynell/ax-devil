"""Opening content changes playback only in the viewer being opened or sought."""

from typing import cast
from unittest.mock import patch

import pytest

from ax_devil.modules.workspace.core import LiveStreamItem, WorkspaceItem
from ax_devil.modules.workspace.ui.browser_rows import RowAction
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.contents import make_live_video, make_video
from tests.helpers.workspace import DummyViewer, content_item
from tests.helpers.workspace_session import LIVE_VIEWER, OFFLINE_VIEWER, ask_row_action, focused_viewer, store_of


class _PlaybackViewer(DummyViewer):
    """Model playback at the workspace's media boundary without opening sources."""

    def _setup_widget_ui(self) -> None:
        self.playing = self.start_frame is None

    def pause_playback(self) -> None:
        """Pause this viewer."""
        self.playing = False

    def show_frame(self, frame: int) -> None:
        """Seek this viewer and leave it paused, as an offline viewer does."""
        super().show_frame(frame)
        self.pause_playback()


def _focused(session: WorkspaceSession) -> _PlaybackViewer:
    return cast(_PlaybackViewer, focused_viewer(session))


@pytest.mark.parametrize("live", [False, True], ids=["offline", "live"])
def test_opening_and_replacing_content_preserves_other_viewers_playback(
    workspace_session: WorkspaceSession, live: bool
) -> None:
    session = workspace_session

    def item(name: str) -> WorkspaceItem:
        return LiveStreamItem(label=name, host="camera.local") if live else content_item(make_video(name))

    with patch(OFFLINE_VIEWER, _PlaybackViewer), patch(LIVE_VIEWER, _PlaybackViewer):
        session.add_items([item("playing"), item("paused")])
        playing = _focused(session)
        playing.set_pinned(True)
        controller = session._workspace_controller
        controller.open_content(store_of(session).contents()[1], to_side=True)
        paused = _focused(session)
        paused.pause_playback()
        paused.set_pinned(True)
        session.add_items([item("preview")])
        preview = _focused(session)
        # Open to Side and Start stream share the workspace opening boundary.
        unopened = item("beside")
        store_of(session).add_items([unopened], open_first=False)
        action = RowAction.START_STREAM if live else RowAction.OPEN_TO_SIDE
        ask_row_action(session, unopened.id, action)
        beside = _focused(session)
        assert playing.playing and not paused.playing and preview.playing
        assert not any(viewer.cleaned_up for viewer in (playing, paused, preview))

        controller.open_content(store_of(session).contents()[1])

    assert preview.cleaned_up
    assert playing.playing and not paused.playing and beside.playing
    assert not any(viewer.cleaned_up for viewer in (playing, paused, beside))


@pytest.mark.parametrize("already_open", [False, True], ids=["new-target", "existing-target"])
def test_bookmark_seeks_only_its_target(workspace_session: WorkspaceSession, already_open: bool) -> None:
    session = workspace_session
    with patch(OFFLINE_VIEWER, _PlaybackViewer), patch(LIVE_VIEWER, _PlaybackViewer):
        session.add_items([content_item(make_live_video()), content_item(make_video("target"))])
        unrelated = _focused(session)
        unrelated.set_pinned(True)
        target = store_of(session).contents()[1]
        if already_open:
            session._workspace_controller.open_content(target, to_side=True)
            sought = _focused(session)
            assert sought.playing
        session._workspace_controller.show_frame(target, 0, 42)
        sought = _focused(session)

    assert unrelated.playing and not unrelated.cleaned_up
    assert not sought.playing
    if already_open:
        assert sought.shown_frames == [42]
    else:
        assert sought.start_frame == 42
