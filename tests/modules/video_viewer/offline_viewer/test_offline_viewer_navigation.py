"""Tests for opening, stepping and closing entries of the offline video viewer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QPoint
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineSession
from ax_devil.modules.workspace.core import (
    PlaylistContent,
    PlaylistEntry,
)
from tests.helpers.offline_sources import (
    TrackedFrameSource,
    assert_frame_source_disposed,
    make_overlay_content,
    make_tracked_frame_source_opener,
)
from tests.helpers.offline_viewer import (
    DeferredBackground,
    OfflineViewerTest,
    assert_no_stale_renderers,
    attach_offline_widget,
    count_frame_viewports,
    lane_displays,
    make_local_content,
    make_seekable_content,
    nav_button,
    nav_label,
    playlist_content,
    shown_entry_index,
    transports,
    warning_text,
)
from tests.helpers.workspace import store_with


class TestOfflineViewerNavigation(OfflineViewerTest):
    """Entries open in the background, step through playlists and release what they opened."""

    def test_construction_defers_initial_entry_load(self, qtbot: QtBot) -> None:
        opened: list[TrackedFrameSource] = []
        content = make_seekable_content("Solo", frame_source_opener=make_tracked_frame_source_opener("Solo", opened))
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        qtbot.addWidget(widget)

        assert widget.get_display_name() == "Solo"
        assert opened == []

        widget.on_workspace_attached()

        assert len(opened) == 1
        assert len(lane_displays(widget)) == 1

    def test_set_display_name_updates_the_name_and_the_header(self, qtbot: QtBot) -> None:
        content = make_seekable_content("Solo")
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        qtbot.addWidget(widget)

        widget.set_display_name("North gate")

        assert widget.get_display_name() == "North gate"
        assert widget._title_label.text() == "North gate"

    def test_consideration_changes_after_cleanup_open_nothing(self, qtbot: QtBot) -> None:
        """A closed viewer ignores workspace consideration updates instead of reopening entries."""
        opened: list[TrackedFrameSource] = []
        videos = [
            make_seekable_content(name, frame_source_opener=make_tracked_frame_source_opener(name, opened))
            for name in ("A", "B")
        ]
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=tuple(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True) for video in videos),
        )
        workspace_store, playlist = store_with(playlist)
        widget = self._open(qtbot, playlist, workspace_store)
        assert len(opened) == 1

        widget.cleanup()
        entry_ref = playlist.entry_consideration_ref(0)
        workspace_store.set_item_considered(entry_ref, False)
        widget.refresh_item_consideration(entry_ref, False)
        lane_ref = playlist.lane_consideration_ref(0, 0)
        widget.refresh_item_consideration(lane_ref, True)

        assert len(opened) == 1
        assert lane_displays(widget) == []

    def test_a_change_to_another_playlist_of_the_same_item_does_not_reopen_the_entry_on_screen(
        self, qtbot: QtBot
    ) -> None:
        opened: list[TrackedFrameSource] = []
        video = make_seekable_content("A", frame_source_opener=make_tracked_frame_source_opener("A", opened))
        entries = (PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),)
        workspace_store, playlist = store_with(PlaylistContent(display_name="Runs", entries=entries))
        widget = self._open(qtbot, playlist, workspace_store)
        other = PlaylistContent(display_name="Other", entries=entries, item_id=playlist.item_id)

        widget.refresh_item_consideration(other.lane_consideration_ref(0, 0), False)

        assert len(opened) == 1

    def test_playlist_navigation_buttons_step_between_entries(self, qtbot: QtBot) -> None:
        widget = self._open(qtbot, playlist_content("A", "B"))
        previous, following = nav_button(widget, "Previous entry"), nav_button(widget, "Next entry")

        assert shown_entry_index(widget) == 0
        assert nav_label(widget).text() == "Entry 1 / 2"
        assert not previous.isEnabled() and following.isEnabled()

        following.click()

        assert shown_entry_index(widget) == 1
        assert nav_label(widget).text() == "Entry 2 / 2"
        assert previous.isEnabled() and not following.isEnabled()

        widget.step_next_entry()
        assert shown_entry_index(widget) == 1

        previous.click()
        assert shown_entry_index(widget) == 0

    def test_next_entry_is_prepared_in_the_background_only_while_the_setting_is_on(
        self, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started: list[object] = []
        stopped: list[bool] = []

        def prepare_entry(request: object) -> object:
            started.append(request)
            return lambda: stopped.append(True)

        monkeypatch.setattr("ax_devil.modules.video_viewer.offline_video_viewer.prepare_entry", prepare_entry)
        settings = GlobalSettings()
        widget = self._open(qtbot, playlist_content("A", "B"))
        assert settings.prepare_next_video is False and started == []

        try:
            settings.prepare_next_video = True
            assert len(started) == 1 and stopped == []
            settings.prepare_next_video = False
            assert stopped == [True]
        finally:
            settings.prepare_next_video = False
        widget.cleanup()

    def test_navigation_controls_position_stays_stable_while_loading(
        self, qtbot: QtBot, deferred_background: DeferredBackground
    ) -> None:
        """Playlist navigation controls stay out of the pane header until the transport bar shows them, and then
        do not shift while an entry opens."""
        widget = self._open(qtbot, playlist_content("A", "B"))
        widget.resize(800, 600)
        widget.show()
        labels: list[QLabel] = widget.findChildren(QLabel)
        assert not [label for label in labels if label.text().startswith("Entry ") and label.isVisible()]
        deferred_background.finish_next()
        QCoreApplication.processEvents()
        nav_bar = nav_label(widget).parentWidget()
        assert nav_bar is not None and nav_bar.isVisible()
        (transport,) = transports(widget)
        assert nav_bar in transport.findChildren(QWidget)
        baseline = nav_bar.mapTo(widget, QPoint(0, 0))

        widget.step_next_entry()
        QCoreApplication.processEvents()
        assert nav_bar.isVisible()
        assert nav_bar.mapTo(widget, QPoint(0, 0)) == baseline

        deferred_background.finish_next()
        QCoreApplication.processEvents()
        assert nav_bar.mapTo(widget, QPoint(0, 0)) == baseline

    def test_stepping_keeps_focus_where_the_user_has_it_while_the_entry_loads(
        self, qtbot: QtBot, deferred_background: DeferredBackground
    ) -> None:
        """Focus on the video returns to it after a step; focus the user moved elsewhere meanwhile is not taken back."""
        window = QWidget()
        qtbot.addWidget(window)
        layout = QVBoxLayout(window)
        search = QLineEdit()
        layout.addWidget(search)
        widget = OfflineVideoViewerWidget(
            playlist_content("A", "B", "C"), render_catalog_manager=self._render_catalog_manager
        )
        layout.addWidget(widget)
        window.show()
        window.activateWindow()
        qtbot.waitUntil(window.isActiveWindow)
        widget.on_workspace_attached()
        deferred_background.finish_next()
        display = lane_displays(widget)[0]
        window.activateWindow()  # Focus needs an active window, and Xvfb has no window manager.
        qtbot.waitUntil(window.isActiveWindow)
        display.viewport.setFocus()
        qtbot.waitUntil(display.viewport.hasFocus)

        widget.step_next_entry()
        deferred_background.finish_next()

        assert shown_entry_index(widget) == 1
        focus = QApplication.focusWidget()
        assert focus is not None and widget.isAncestorOf(focus)

        widget.step_next_entry()
        search.setFocus()
        qtbot.waitUntil(search.hasFocus)
        deferred_background.finish_next()

        assert shown_entry_index(widget) == 2 and len(lane_displays(widget)) == 1
        assert QApplication.focusWidget() is search

    def test_playlist_navigation_skips_disabled_entries(self, qtbot: QtBot) -> None:
        playlist = playlist_content("A", "B", "C")
        workspace_store, playlist = store_with(playlist)
        workspace_store.set_item_considered(playlist.entry_consideration_ref(1), False)
        widget = self._open(qtbot, playlist, workspace_store)

        widget.step_next_entry()
        assert shown_entry_index(widget) == 2

        widget.step_prev_entry()
        assert shown_entry_index(widget) == 0

    def test_playlist_starts_on_first_enabled_entry(self, qtbot: QtBot) -> None:
        playlist = playlist_content("A", "B")
        workspace_store, playlist = store_with(playlist)
        workspace_store.set_item_considered(playlist.entry_consideration_ref(0), False)
        widget = self._open(qtbot, playlist, workspace_store)

        assert shown_entry_index(widget) == 1

    @pytest.mark.parametrize("start_frame", [None, 5])
    def test_explicit_open_keeps_a_hidden_entry_and_bookmarks_show_the_requested_frame(
        self, qtbot: QtBot, deferred_background: DeferredBackground, start_frame: int | None
    ) -> None:
        """Explicit Open plays a hidden entry; a bookmark shows its frame paused without unhiding it."""
        playlist = playlist_content("A", "B")
        workspace_store, playlist = store_with(playlist)
        workspace_store.set_item_considered(playlist.entry_consideration_ref(0), False)
        widget = OfflineVideoViewerWidget(
            playlist,
            consideration_query=workspace_store,
            render_catalog_manager=self._render_catalog_manager,
            start_index=0,
            start_frame=start_frame,
        )
        attach_offline_widget(qtbot, widget)

        assert widget.bookmark_position() is None
        assert widget.playhead_frame() == (start_frame or 0)
        deferred_background.finish_next()
        assert shown_entry_index(widget) == 0
        assert widget._runtime is not None and widget._runtime.playback.current_frame == (start_frame or 0)
        assert bool(widget._runtime.playback.is_playing) == (start_frame is None)
        assert not workspace_store.is_item_considered(playlist.entry_consideration_ref(0))

        widget.step_next_entry()
        deferred_background.finish_next()
        widget.step_prev_entry()
        assert shown_entry_index(widget) == 1

    def test_source_paths_are_the_overlay_files_of_the_considered_lanes_on_screen(self, qtbot: QtBot) -> None:
        """A hidden lane's overlay file is not watched for the entry on screen, so rewriting it offers no reload."""
        video = make_seekable_content("A", overlays=(make_overlay_content("first"), make_overlay_content("second")))
        workspace_store, video = store_with(video)
        widget = self._open(qtbot, video, workspace_store)
        assert widget.source_paths().files == {Path("/tmp/first.txt"), Path("/tmp/second.txt")}

        workspace_store.set_item_considered(video.lane_consideration_ref(0, 1), False)

        assert widget.source_paths().files == {Path("/tmp/first.txt")}

    def test_excluding_current_playlist_entry_navigates_to_next_considered(self, qtbot: QtBot) -> None:
        playlist = playlist_content("A", "B")
        workspace_store, playlist = store_with(playlist)
        widget = self._open(qtbot, playlist, workspace_store)
        assert shown_entry_index(widget) == 0

        entry_ref = playlist.entry_consideration_ref(0)
        workspace_store.set_item_considered(entry_ref, False)
        widget.refresh_item_consideration(entry_ref, False)

        assert shown_entry_index(widget) == 1

    def test_navigation_failure_warns_and_stays_on_failed_entry(self, qtbot: QtBot) -> None:
        """A failed entry leaves the viewer empty on it, with its opened sources released, and stepping still works."""
        opened: list[TrackedFrameSource] = []

        def broken_open() -> object:
            raise RuntimeError("broken entry")

        v1 = make_local_content("A")
        v2 = make_seekable_content("B", frame_source_opener=make_tracked_frame_source_opener("B", opened))
        v3 = make_seekable_content("C", frame_source_opener=broken_open)
        entry_with_failure = PlaylistEntry(
            lanes=(*v2.standalone_lanes(), *v3.standalone_lanes()),
            default_considered=True,
        )
        playlist = PlaylistContent(
            display_name="Test",
            entries=(PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True), entry_with_failure),
        )
        widget = self._open(qtbot, playlist)

        widget.step_next_entry()

        assert shown_entry_index(widget) == 1
        assert lane_displays(widget) == []
        assert "broken entry" in warning_text(widget)
        assert len(opened) == 1
        assert_frame_source_disposed(opened[0])

        widget.step_prev_entry()

        assert shown_entry_index(widget) == 0
        assert len(lane_displays(widget)) == 1

    def test_navigating_while_loading_opens_the_newest_entry(
        self, qtbot: QtBot, deferred_background: DeferredBackground
    ) -> None:
        """Stepping again while an entry opens replaces it: the older entry opens nothing more and is never shown."""
        sources: dict[str, list[TrackedFrameSource]] = {"A": [], "B": [], "C": []}
        contents = [
            make_seekable_content(name, frame_source_opener=make_tracked_frame_source_opener(name, registry))
            for name, registry in sources.items()
        ]
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=tuple(
                PlaylistEntry(lanes=content.standalone_lanes(), default_considered=True) for content in contents
            ),
        )
        widget = self._open(qtbot, playlist)
        deferred_background.finish_next()
        assert len(lane_displays(widget)) == 1

        widget.step_next_entry()
        assert lane_displays(widget) == []
        assert_frame_source_disposed(sources["A"][0])
        widget.step_next_entry()
        assert shown_entry_index(widget) == 2
        assert nav_label(widget).text() == "Entry 3 / 3"

        deferred_background.finish_next()
        assert lane_displays(widget) == []
        assert sources["B"] == []

        deferred_background.finish_next()
        assert len(lane_displays(widget)) == 1
        assert len(sources["C"]) == 1
        assert sources["C"][0].stop_calls == 0

    def test_cleanup_while_loading_never_installs_the_entry(
        self, qtbot: QtBot, deferred_background: DeferredBackground
    ) -> None:
        """Closing the viewer mid-open does not wait for the open, and the entry is never shown or left open."""
        opened: list[TrackedFrameSource] = []
        content = make_seekable_content("A", frame_source_opener=make_tracked_frame_source_opener("A", opened))
        widget = self._open(qtbot, content)

        widget.cleanup()
        deferred_background.finish_next()

        assert lane_displays(widget) == []
        assert all(source.stop_calls == 1 for source in opened)

    def test_display_build_failure_warns_and_releases_the_entry(self, qtbot: QtBot) -> None:
        """A failure while building an entry's displays is reported like a failed open, with its sources released."""
        opened: list[TrackedFrameSource] = []
        content = make_seekable_content("A", frame_source_opener=make_tracked_frame_source_opener("A", opened))
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        qtbot.addWidget(widget)

        with patch.object(OfflineSession, "build", side_effect=RuntimeError("broken display")):
            widget.on_workspace_attached()

        assert lane_displays(widget) == []
        assert "broken display" in warning_text(widget)
        assert len(opened) == 1
        assert_frame_source_disposed(opened[0])

    def test_entry_navigation_leaves_no_stale_renderers(self, qtbot: QtBot) -> None:
        """Offline entry rebuilds should keep renderers scoped to the shown lanes."""
        first = make_local_content("A", with_overlay=True)
        second = make_seekable_content("B", overlays=(make_overlay_content("o1"), make_overlay_content("o2")))
        playlist = PlaylistContent(
            display_name="playlist",
            entries=(
                PlaylistEntry(lanes=first.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=second.standalone_lanes(), default_considered=True),
            ),
        )
        baseline_frame_viewports = count_frame_viewports()
        widget = self._open(qtbot, playlist)
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.step_next_entry()
        assert len(lane_displays(widget)) == 2
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.step_prev_entry()
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.cleanup()
        assert count_frame_viewports() == baseline_frame_viewports

    def test_selected_playback_speed_is_reapplied_after_entry_switch(self, qtbot: QtBot) -> None:
        frame_sources: list[TrackedFrameSource] = []
        videos = [
            make_seekable_content(name, frame_source_opener=make_tracked_frame_source_opener(name, frame_sources))
            for name in ("A", "B")
        ]
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=tuple(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True) for video in videos),
        )
        widget = self._open(qtbot, playlist)

        widget.set_playback_speed(0.2)
        assert frame_sources[0].speed_updates[-1] == 0.2
        widget.step_next_entry()

        assert frame_sources[-1].speed_updates[-1] == 0.2
