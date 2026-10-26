"""Tests for the playback transport and timeline of the offline video viewer."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QPoint, QPropertyAnimation
from PySide6.QtWidgets import (
    QLabel,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.workspace.core import (
    EntryLane,
    PlaylistContent,
    PlaylistEntry,
)
from tests.helpers.offline_sources import (
    TrackedFrameSource,
    TrackedOverlaySource,
    frame_data,
    make_overlay_content,
    make_tracked_frame_source_opener,
    stub_frame_source,
)
from tests.helpers.offline_viewer import (
    OfflineViewerTest,
    lane_displays,
    lane_entry,
    make_local_content,
    make_seekable_content,
    shown_entry_index,
    transports,
)
from tests.helpers.workspace import store_with


class TestOfflineViewerPlayback(OfflineViewerTest):
    """One or several lanes follow one timeline under one transport."""

    def test_single_video_pane_names_it_once_and_describes_it_in_the_header(self, qtbot: QtBot) -> None:
        """A lane named like its pane shows no name on the video; the header shows the video's size, rate and length."""
        source = stub_frame_source()
        source.get_duration_s.return_value = 4.0
        widget = self._open(qtbot, make_seekable_content("Solo", frame_source_opener=lambda: source))
        widget.resize(900, 600)
        widget.show()

        assert widget.findChild(QLabel, "lane-indicator-label") is None
        assert widget.header_details() == "640×480 · 25 fps · 0:04"
        (controls,) = transports(widget)
        assert controls.timeline_slider.cached_ranges() == ((0, 9),)

    def test_closing_the_viewer_stops_transport_fading(self, qtbot: QtBot) -> None:
        """A closed playlist viewer's shared transport stops fading, so no animation or timer outlives its widgets."""
        videos = [make_seekable_content(name) for name in ("A", "B")]
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=tuple(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True) for video in videos),
        )
        widget = self._open(qtbot, playlist)
        (controls,) = transports(widget)
        controls.start_auto_hide_timer()
        controls.fade_out()
        assert controls.hide_timer.isActive()
        assert controls.fade_animation.state() == QPropertyAnimation.State.Running

        widget.cleanup()
        controls.fade_in()
        controls.start_auto_hide_timer()

        assert not controls.hide_timer.isActive()
        assert controls.fade_animation.state() == QPropertyAnimation.State.Stopped

    def test_single_video_controls_drive_playback(self, qtbot: QtBot) -> None:
        source = stub_frame_source()
        widget = self._open(qtbot, make_seekable_content("Solo", frame_source_opener=lambda: source))
        (controls,) = widget.findChildren(SeekableVideoControlPanel)

        controls.frameStepRequested.emit(10)
        controls.jumpToRequested.emit(42)
        controls.playRequested.emit()
        controls.pauseRequested.emit()
        controls.set_playback_speed(1.1)

        source.jump_to.assert_any_call(10)
        source.jump_to.assert_any_call(42)
        source.play.assert_called()
        source.pause.assert_called()
        source.set_playback_speed.assert_any_call(1.1)

    def test_shared_video_multi_overlay_lanes_follow_one_timeline(self, qtbot: QtBot) -> None:
        """Lanes comparing overlays on one video share its source and one transport, and look up the shown frame."""
        frame_sources: list[TrackedFrameSource] = []
        overlay_sources: list[TrackedOverlaySource] = []
        content = make_seekable_content(
            "multi-overlay",
            frame_source_opener=make_tracked_frame_source_opener("video", frame_sources),
            overlays=(
                make_overlay_content("first", overlay_sources),
                make_overlay_content("second", overlay_sources),
            ),
        )
        widget = self._open(qtbot, content)

        assert len(frame_sources) == 1
        assert len(overlay_sources) == 2
        assert len(lane_displays(widget)) == 2
        (controls,) = widget.findChildren(SeekableVideoControlPanel)
        assert not controls.isHidden()
        assert controls.timeline_slider.maximum() == 99
        (source,) = frame_sources

        def shown_frames() -> tuple[int, list[int]]:
            return controls.timeline_slider.value(), [
                overlay.requested_frame_ids[-1].sequence_id for overlay in overlay_sources
            ]

        source.frameReady.emit(frame_data(7))
        QCoreApplication.processEvents()
        assert shown_frames() == (7, [7, 7])

        controls.jumpToRequested.emit(12)
        assert source.get_current_frame() == 12
        source.frameReady.emit(frame_data(12))
        QCoreApplication.processEvents()
        assert shown_frames() == (12, [12, 12])

        widget.step_frames(3)
        assert source.get_current_frame() == 15

        controls.set_playback_speed(1.1)
        assert source.speed_updates[-1] == 1.1
        widget.set_playback_speed(1.7)
        assert source.speed_updates[-1] == 1.7

    def test_multi_video_lanes_follow_primary_frame_index(self, qtbot: QtBot) -> None:
        """Distinct video lanes should asynchronously render the primary timeline frame."""
        frame_sources: list[TrackedFrameSource] = []
        overlay_sources: list[TrackedOverlaySource] = []
        videos = [
            make_seekable_content(
                name,
                frame_source_opener=make_tracked_frame_source_opener(name, frame_sources),
                overlays=(make_overlay_content(f"overlay-{name}", overlay_sources),),
            )
            for name in ("A", "B")
        ]
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=tuple(
                        EntryLane(
                            display_name=video.display_name,
                            video=video,
                            overlay=video.overlays[0],
                            default_considered=True,
                        )
                        for video in videos
                    ),
                    default_considered=True,
                ),
            ),
        )
        widget = self._open(qtbot, playlist)

        assert len(lane_displays(widget)) == 2
        assert len(frame_sources) == 2
        assert len(overlay_sources) == 2

        frame_sources[0].frameReady.emit(frame_data(7))
        QCoreApplication.processEvents()

        assert frame_sources[1].async_frame_requests[-1] == 7
        assert [source.requested_frame_ids[-1].sequence_id for source in overlay_sources] == [7, 7]

    def test_entry_without_considered_lanes_shows_placeholder_and_clears_the_shared_controls(
        self, qtbot: QtBot
    ) -> None:
        """Stepping to an entry whose lanes are all excluded leaves no frames, time or cache from the previous one."""
        playlist = PlaylistContent(
            display_name="Playlist",
            entries=(
                PlaylistEntry(
                    lanes=make_local_content("A", with_overlay=True).standalone_lanes(), default_considered=True
                ),
                PlaylistEntry(
                    lanes=make_local_content("B", with_overlay=True).standalone_lanes(), default_considered=True
                ),
            ),
        )
        workspace_store, playlist = store_with(playlist)
        workspace_store.set_item_considered(playlist.lane_consideration_ref(1, 0), False)
        widget = self._open(qtbot, playlist, workspace_store)
        widget.show()
        (controls,) = transports(widget)
        assert controls.timeline_slider.maximum() > 0

        widget.step_next_entry()
        QCoreApplication.processEvents()

        assert lane_displays(widget) == []
        placeholder = [
            label for label in widget.findChildren(QLabel) if label.text() == "No considered lanes in this entry"
        ]
        assert len(placeholder) == 1 and placeholder[0].isVisible()
        assert controls.timeline_slider.maximum() == 0
        assert controls.timeline_slider.cached_ranges() == ()
        time = controls.timecode_text()
        assert time == "" or not any(
            label.isVisibleTo(controls) for label in controls.findChildren(QLabel) if label.text() == time
        )
        assert widget.header_details() == ""

    def test_multi_lane_entries_share_one_transport_over_the_viewer(self, qtbot: QtBot) -> None:
        """Every playlist entry, one lane or several, gets one shared transport over the bottom of the video."""
        v1 = make_local_content("A")
        v2 = make_local_content("B")
        playlist = PlaylistContent(
            display_name="mixed",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                lane_entry(v1, v2),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        widget = self._open(qtbot, playlist)
        widget.resize(800, 600)
        widget.show()

        for entry_index, lane_count in enumerate((1, 2, 1)):
            assert shown_entry_index(widget) == entry_index
            QCoreApplication.processEvents()
            displays = lane_displays(widget)
            assert len(displays) == lane_count
            assert len(widget.findChildren(SeekableVideoControlPanel)) == 1
            (transport,) = transports(widget)
            transport_rect = transport.rect().translated(transport.mapTo(widget, QPoint(0, 0)))
            video_bottom = max(
                display.rect().translated(display.mapTo(widget, QPoint(0, 0))).bottom() for display in displays
            )
            assert widget.rect().contains(transport_rect)
            assert transport_rect.bottom() == video_bottom
            widget.step_next_entry()
