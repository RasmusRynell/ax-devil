"""Tests for the lanes of the offline video viewer: layout, names, zoom and lane toggles."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.video_player.orchestration.fullscreen import LaneFullscreenController
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.core import (
    EntryLane,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from tests.helpers.offline_sources import (
    TrackedFrameSource,
    TrackedOverlaySource,
    assert_frame_source_disposed,
    assert_overlay_source_disposed,
    make_overlay_content,
    make_tracked_frame_source_opener,
)
from tests.helpers.offline_viewer import (
    OfflineViewerTest,
    assert_no_stale_renderers,
    attach_offline_widget,
    count_frame_viewports,
    lane_displays,
    lane_entry,
    make_local_content,
    make_seekable_content,
    playlist_content,
    shown_entry_index,
)
from tests.helpers.qt_events import make_wheel_event
from tests.helpers.workspace import store_with


class TestOfflineViewerLanes(OfflineViewerTest):
    """Lane geometry, names, zoom and the rebuilds lane toggles cause."""

    @pytest.mark.parametrize("lane_count, columns", [(2, 2), (3, 2), (5, 3)])
    def test_lane_geometry(self, qtbot: QtBot, lane_count: int, columns: int) -> None:
        """Lanes fill equal visible cells, wrap into rows without overlap and keep usable widths when shrunk."""
        playlist = PlaylistContent(
            display_name="comparison",
            entries=(lane_entry(*(make_local_content(f"Video {index}") for index in range(lane_count))),),
        )
        widget = self._open(qtbot, playlist)
        widget.show()
        displays = lane_displays(widget)
        assert len(displays) == lane_count
        panes = [display.parentWidget() for display in displays]
        container = panes[0].parentWidget() if panes[0] is not None else None
        assert container is not None and all(pane is not None for pane in panes)
        for width in (1200, 400):
            widget.resize(width, 700)
            QApplication.processEvents()
            rects = [pane.geometry() for pane in panes if pane is not None]
            assert all(display.isVisible() for display in displays)
            assert max(rect.width() for rect in rects) - min(rect.width() for rect in rects) <= 1
            assert all(container.rect().contains(rect) for rect in rects)
            assert all(display.width() >= display.minimumSizeHint().width() for display in displays)
            for index, rect in enumerate(rects):
                assert rect.y() == rects[index // columns * columns].y()
                if index % columns:
                    assert rect.x() > rects[index - 1].right()
                if index >= columns:
                    assert rect.y() > rects[index - columns].bottom()

    def test_keyboard_zoom_preserves_fullscreen_lane_center(self, qtbot: QtBot) -> None:
        """Zoom uses the focused lane's pan even when a smaller peer has clamped it away."""
        playlist = PlaylistContent(
            display_name="multi", entries=(lane_entry(make_local_content("A"), make_local_content("B")),)
        )
        widget = self._open(qtbot, playlist)
        widget.resize(700, 1000)
        widget.show()
        widget.activateWindow()
        qtbot.waitUntil(widget.isActiveWindow)
        first, second = lane_displays(widget)
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.fill(0)
        frame = VideoFrameWithOverlays(VideoFrame(image, 0.0, 0), None)
        first.display_frame(frame)
        second.display_frame(frame)
        fullscreen = LaneFullscreenController(widget, [])
        second.viewport.setFocus()
        fullscreen.toggle()
        qtbot.waitUntil(second.window().isActiveWindow)
        qtbot.waitUntil(second.viewport.hasFocus)
        try:
            anchor = QPointF(second.viewport.width() / 2, 80)
            for _ in range(6):
                second.viewport.wheelEvent(make_wheel_event(anchor, 120))
            base = second.viewport.frame_display_rect()
            assert base is not None
            before = second.viewport.viewport_state.to_normalized(base)
            assert before.pan_y != 0
            assert first.viewport.viewport_state.pan_offset.y() == 0

            widget.zoom(ZoomStep.IN)

            after = second.viewport.viewport_state.to_normalized(base)
            assert after.zoom > before.zoom
            assert after.pan_y == pytest.approx(before.pan_y)
            assert first.viewport.viewport_state.zoom_level == after.zoom

            widget.zoom(ZoomStep.RESET)

            for display in (first, second):
                assert display.viewport.viewport_state.zoom_level == 1.0
                assert display.viewport.viewport_state.pan_offset == QPointF(0, 0)
        finally:
            fullscreen.exit()
            widget.cleanup()

    def test_lane_names_follow_visible_video_and_hide_for_info_or_when_turned_off(self, qtbot: QtBot) -> None:
        """Lane names sit in the visible video's top-left corner; the info overlay or the preference hides them."""
        playlist = PlaylistContent(
            display_name="multi", entries=(lane_entry(make_local_content("A"), make_local_content("B")),)
        )
        widget = self._open(qtbot, playlist)
        displays = lane_displays(widget)
        names = [display.viewport.findChild(QLabel, "lane-indicator-label") for display in displays]
        assert [name.text() for name in names if name is not None] == ["A", "B"]

        def shown() -> list[bool]:
            return [name is not None and not name.isHidden() for name in names]

        widget.toggle_info_overlay()
        assert shown() == [False, False]
        widget.toggle_info_overlay()
        assert shown() == [True, True]

        widget.resize(700, 1000)
        widget.show()
        qtbot.waitExposed(widget)
        first_viewport = displays[0].viewport
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.fill(0)
        first_viewport.display_frame(VideoFrameWithOverlays(VideoFrame(image=image, timestamp=0.0), overlays=None))
        first_name = names[0]
        assert first_name is not None
        bars = first_viewport.visible_frame_rect()
        assert bars is not None and bars.top() > 0
        assert first_name.pos() == QPoint(round(bars.left()) + 10, round(bars.top()) + 10)
        fill_zoom = first_viewport.height() / bars.height()
        while first_viewport.viewport_state.zoom_level < fill_zoom:
            first_viewport.wheelEvent(make_wheel_event(QPointF(100, 80), 120))
        assert first_viewport.viewport_state.zoom_level > 1.0
        assert shown() == [True, True]
        assert first_name.pos() == QPoint(10, 10)

        settings = GlobalSettings()
        settings.set_overlay_enabled(OverlayPreference.LANE_NAMES, False)
        try:
            assert shown() == [False, False]
            widget.toggle_info_overlay()
            widget.toggle_info_overlay()
            assert shown() == [False, False]
        finally:
            settings.set_overlay_enabled(OverlayPreference.LANE_NAMES, True)
        assert shown() == [True, True]

    def test_lane_toggles_rebuild_compared_videos_and_release_old_sources(self, qtbot: QtBot) -> None:
        """Excluding and restoring a lane rebuilds the entry, releasing every replaced source and renderer."""
        get_render_metrics_store().clear()
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
        workspace_store, playlist = store_with(playlist)
        baseline_frame_viewports = count_frame_viewports()
        widget = self._open(qtbot, playlist, workspace_store)
        assert len(frame_sources) == 2
        assert len(overlay_sources) == 2
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        lane_ref = playlist.lane_consideration_ref(0, 1)
        workspace_store.set_item_considered(lane_ref, False)
        widget.refresh_item_consideration(lane_ref, False)

        assert len(lane_displays(widget)) == 1
        assert len(frame_sources) == 3
        assert len(overlay_sources) == 3
        for source in frame_sources[:2]:
            assert_frame_source_disposed(source)
        for overlay in overlay_sources[:2]:
            assert_overlay_source_disposed(overlay)
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        workspace_store.set_item_considered(lane_ref, True)
        widget.refresh_item_consideration(lane_ref, True)

        assert len(lane_displays(widget)) == 2
        assert len(frame_sources) == 5
        assert len(overlay_sources) == 5
        assert_frame_source_disposed(frame_sources[2])
        assert_overlay_source_disposed(overlay_sources[2])
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.cleanup()
        assert get_render_metrics_store().snapshot() == ()
        assert count_frame_viewports() == baseline_frame_viewports

    def test_lane_toggles_rebuild_shared_video_lanes_and_release_old_sources(self, qtbot: QtBot) -> None:
        """Lanes comparing overlays on one video keep sharing one source across rebuilds and release the old ones."""
        get_render_metrics_store().clear()
        frame_sources: list[TrackedFrameSource] = []
        overlay_sources: list[TrackedOverlaySource] = []
        content = make_seekable_content(
            "multi-overlay",
            frame_source_opener=make_tracked_frame_source_opener("video", frame_sources),
            overlays=tuple(make_overlay_content(name, overlay_sources) for name in ("first", "second", "third")),
        )
        workspace_store, content = store_with(content)
        baseline_frame_viewports = count_frame_viewports()
        widget = self._open(qtbot, content, workspace_store)
        assert len(frame_sources) == 1
        assert len(overlay_sources) == 3
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        lane_ref = content.lane_consideration_ref(0, 1)
        workspace_store.set_item_considered(lane_ref, False)
        widget.refresh_item_consideration(lane_ref, False)

        assert len(lane_displays(widget)) == 2
        assert len(frame_sources) == 2
        assert len(overlay_sources) == 5
        assert_frame_source_disposed(frame_sources[0])
        for overlay in overlay_sources[:3]:
            assert_overlay_source_disposed(overlay)
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        workspace_store.set_item_considered(lane_ref, True)
        widget.refresh_item_consideration(lane_ref, True)

        assert len(lane_displays(widget)) == 3
        assert len(frame_sources) == 3
        assert len(overlay_sources) == 8
        assert_frame_source_disposed(frame_sources[1])
        assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.cleanup()
        assert get_render_metrics_store().snapshot() == ()
        assert count_frame_viewports() == baseline_frame_viewports


class TestMediaToolsToggle:
    """The Tools toggle opens the focused lane's media tools; new viewers and entries start with them closed."""

    @pytest.fixture(autouse=True)
    def _set_render_catalog_manager(self, render_catalog_manager: SceneRenderCatalogManager) -> None:
        self._render_catalog_manager = render_catalog_manager

    def _open_viewer(self, qtbot: QtBot, content: SeekableVideoContent | PlaylistContent) -> OfflineVideoViewerWidget:
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        attach_offline_widget(qtbot, widget)
        return widget

    def test_tools_toggle_opens_the_focused_lane_and_tracks_drag_handle_changes(self, qtbot: QtBot) -> None:
        content = make_seekable_content(
            "lanes", overlays=(make_overlay_content("first"), make_overlay_content("second"))
        )
        widget = self._open_viewer(qtbot, content)
        widget.show()
        widget.activateWindow()
        qtbot.waitUntil(widget.isActiveWindow)
        displays = lane_displays(widget)

        assert [display.is_side_panel_open() for display in displays] == [False, False]

        displays[1].viewport.setFocus()
        qtbot.waitUntil(displays[1].viewport.hasFocus)
        widget.toggle_media_tools()

        assert [display.is_side_panel_open() for display in displays] == [False, True]

        widget.toggle_media_tools()
        assert [display.is_side_panel_open() for display in displays] == [False, False]

        # With focus outside the videos, the toggle acts on the first lane.
        widget.setFocus()
        qtbot.waitUntil(widget.hasFocus)
        widget.toggle_media_tools()
        assert [display.is_side_panel_open() for display in displays] == [True, False]

    def test_next_playlist_entry_starts_with_tools_closed(self, qtbot: QtBot) -> None:
        widget = self._open_viewer(qtbot, playlist_content("A", "B"))
        widget.toggle_media_tools()
        assert [display.is_side_panel_open() for display in lane_displays(widget)] == [True]

        widget.step_next_entry()

        assert shown_entry_index(widget) == 1
        assert [display.is_side_panel_open() for display in lane_displays(widget)] == [False]

    def test_new_viewer_starts_closed_after_another_viewer_opened_tools(self, qtbot: QtBot) -> None:
        first = self._open_viewer(qtbot, make_local_content("first", with_overlay=True))
        first.toggle_media_tools()

        second = self._open_viewer(qtbot, make_local_content("second", with_overlay=True))
        assert [display.is_side_panel_open() for display in lane_displays(second)] == [False]
