"""Browser lane activation retains playback and owns focus across entry loading."""

from __future__ import annotations

from typing import cast

import pytest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.core import PlaylistContent, PlaylistEntry, SeekableVideoContent
from ax_devil.modules.workspace.ui.browser_rows import RowAction, WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from tests.helpers.offline_sources import TrackedFrameSource, make_overlay_content
from tests.helpers.offline_viewer import DeferredBackground, make_seekable_content, shown_session
from tests.helpers.workspace import store_with


def _lane_rows(controller: WorkspaceController, playlist: bool) -> tuple[WorkspaceBrowserRow, ...]:
    row = controller.sections()[0].rows[0]
    return row.children[0].children if playlist else row.children


@pytest.mark.parametrize("playlist", [False, True], ids=["standalone", "playlist"])
def test_browser_opens_exact_lane_and_reuses_active_matching_viewer_without_seeking(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, playlist: bool
) -> None:
    video = make_seekable_content(
        "comparison",
        frame_source_opener=lambda: TrackedFrameSource("comparison"),
        overlays=tuple(make_overlay_content(str(i)) for i in range(3)),
    )
    content: PlaylistContent | SeekableVideoContent
    if playlist:
        store, content = store_with(
            PlaylistContent(display_name="Runs", entries=(PlaylistEntry(video.standalone_lanes(), True),))
        )
    else:
        store, content = store_with(video)
    # The requested lane is the second visible lane, but its original entry index is 2.
    store.set_item_considered(content.lane_consideration_ref(0, 0), False)
    entry_ref = content.entry_consideration_ref(0)
    if entry_ref is not None:
        store.set_item_considered(entry_ref, False)
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    split = SplitView(window)
    layout.addWidget(split)
    controller = WorkspaceController(store, split, render_catalog_manager, window)
    window.resize(1200, 700)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    controller.perform_row_action(_lane_rows(controller, playlist)[2], RowAction.OPEN)
    viewer = cast(OfflineVideoViewerWidget, split.get_focused_widget())
    runtime = shown_session(viewer)
    window.activateWindow()
    qtbot.waitUntil(lambda: runtime.focused_display() is runtime.lanes[1].display)
    assert runtime.lanes[1].lane_index == 2
    assert runtime.playback.is_playing
    viewer.show_frame(7)
    controller.perform_row_action(_lane_rows(controller, playlist)[1], RowAction.OPEN_TO_SIDE)
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    duplicate = cast(OfflineVideoViewerWidget, split.get_focused_widget())
    assert duplicate is not viewer and duplicate.is_pinned()
    duplicate.show_frame(13)
    for playing in (False, True):
        if playing:
            duplicate.toggle_playback()
        frame = shown_session(duplicate).playback.current_frame
        controller.perform_row_action(_lane_rows(controller, playlist)[2], RowAction.OPEN)
        assert split.get_widget_count() == 2 and split.get_focused_widget() is duplicate
        assert shown_session(duplicate).playback.current_frame == frame
        assert shown_session(duplicate).playback.is_playing is playing
        assert shown_session(duplicate).focused_display() is shown_session(duplicate).lanes[1].display
        assert shown_session(viewer).playback.current_frame == 7 and not shown_session(viewer).playback.is_playing
    if entry_ref is not None:
        assert not store.is_item_considered(entry_ref)


def test_hidden_lane_requires_explicit_unhide_and_focus_survives_reload(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, deferred_background: DeferredBackground
) -> None:
    video = make_seekable_content("hidden", overlays=tuple(make_overlay_content(str(i)) for i in range(2)))
    store, video = store_with(video)
    store.set_item_considered(video.lane_consideration_ref(0, 1), False)
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    split = SplitView(window)
    layout.addWidget(split)
    controller = WorkspaceController(store, split, render_catalog_manager, window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    hidden = _lane_rows(controller, False)[1]
    assert hidden.consideration_ref is not None
    controller.perform_row_action(hidden, RowAction.OPEN)
    controller.perform_row_action(hidden, RowAction.OPEN_TO_SIDE)
    assert split.get_widget_count() == 0 and not store.is_item_considered(hidden.consideration_ref)
    controller.open_content(video)
    deferred_background.finish_next()
    viewer = cast(OfflineVideoViewerWidget, split.get_focused_widget())
    controller.perform_row_action(hidden, RowAction.UNHIDE_AND_OPEN)
    assert split.get_focused_widget() is viewer and split.get_widget_count() == 1
    deferred_background.finish_next()
    runtime = shown_session(viewer)
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is runtime.lanes[1].display.viewport)
    assert store.is_item_considered(video.lane_consideration_ref(0, 1))


def test_delayed_lane_open_cannot_take_focus_back_from_another_pane(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, deferred_background: DeferredBackground
) -> None:
    video = make_seekable_content("slow", overlays=tuple(make_overlay_content(str(i)) for i in range(2)))
    store, video = store_with(video)
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    split = SplitView(window)
    layout.addWidget(split)
    controller = WorkspaceController(store, split, render_catalog_manager, window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    controller.perform_row_action(_lane_rows(controller, False)[1], RowAction.OPEN_TO_SIDE)
    first = cast(OfflineVideoViewerWidget, split.get_focused_widget())
    controller.perform_row_action(_lane_rows(controller, False)[0], RowAction.OPEN_TO_SIDE)
    second = cast(OfflineVideoViewerWidget, split.get_focused_widget())
    deferred_background.finish_next()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    assert split.get_focused_widget() is second
    deferred_background.finish_next()
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is shown_session(second).lanes[0].display.viewport)
    focus = QApplication.focusWidget()
    assert focus is not None and not first.isAncestorOf(focus)
