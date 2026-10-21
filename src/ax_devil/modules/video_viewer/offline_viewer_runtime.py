"""Runtime orchestration for offline video viewer entries."""

from __future__ import annotations

from contextlib import ExitStack

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ax_devil.core.data_types import FrameData
from ax_devil.modules.background_jobs import BackgroundJobs, JobScope, background_jobs
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.scene.rendering import (
    OverlayVisibility,
    SceneRenderCatalogManager,
)
from ax_devil.modules.video_player.engine.viewport_state import NormalizedViewport, ZoomStep
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.offline_entry_media import (
    EntryMedia,
    release_media,
)
from ax_devil.modules.video_viewer.offline_export import run_lane_export
from ax_devil.modules.video_viewer.offline_lane import OfflineLane
from ax_devil.modules.video_viewer.offline_lane_builder import (
    build_lane,
    create_lane_layout,
)
from ax_devil.modules.video_viewer.offline_playback import OfflinePlayback

# Whole-file analysis of a lane's overlay; its own pool keeps it from delaying the shared one.
_ANALYSIS_POOL = "overlay-analysis"


class OfflineSession(QObject):
    """One active offline entry runtime: frame displays built over already opened media, which it owns."""

    def __init__(
        self,
        lanes: list[OfflineLane],
        container: QWidget,
        *,
        media: EntryMedia,
        playback: OfflinePlayback,
        analysis_jobs: JobScope,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.lanes = lanes
        self.container = container
        self._media = media
        self.playback = playback
        playback.setParent(self)
        self._analysis_jobs = analysis_jobs
        playback.frameReady.connect(self._present_frame)
        playback.playingChanged.connect(self._show_playing)
        playback.seekStarted.connect(self._refresh_scenes)

    def _present_frame(self, source_index: int, frame: FrameData) -> None:
        for lane in self.lanes:
            if lane.source_index == source_index:
                lane.present_frame(frame)

    def _show_playing(self, playing: bool) -> None:
        for lane in self.lanes:
            lane.tools_panel.set_playing(playing)

    def _refresh_scenes(self) -> None:
        for lane in self.lanes:
            lane.tools_panel.refresh_scene()

    @property
    def displays(self) -> tuple[FrameDisplay, ...]:
        """Return all active lane displays in presentation order."""
        return tuple(lane.display for lane in self.lanes)

    def focused_display(self) -> FrameDisplay | None:
        """Return the display of the focused lane, or the first lane's when focus is outside the displays."""
        displays = self.displays
        if not displays:
            return None
        focus = QApplication.focusWidget()
        return next(
            (
                display
                for display in displays
                if focus is not None and (display is focus or display.isAncestorOf(focus))
            ),
            displays[0],
        )

    def zoom(self, step: ZoomStep) -> None:
        """Zoom the focused lane."""
        display = self.focused_display()
        if display is not None:
            display.zoom(step)

    @property
    def has_multiple_lanes(self) -> bool:
        """Return whether the active runtime displays multiple lanes."""
        return len(self.lanes) > 1

    @classmethod
    def build(
        cls,
        parent_widget: QWidget,
        media: EntryMedia,
        *,
        render_catalog_manager: SceneRenderCatalogManager,
        use_lane_controls: bool = True,
        lane_visibility: dict[int, OverlayVisibility] | None = None,
        pane_title: str = "",
        jobs: BackgroundJobs | None = None,
    ) -> "OfflineSession":
        """Build the displays for one entry over its opened *media*; the session then owns the media.

        Only creates widgets, so it never waits for file work. *lane_visibility* holds overlay visibility by original
        lane position; each lane starts from its entry and writes its changes back, so choices carry over to the next
        entry. If building fails, the caller keeps the media.
        """
        with ExitStack() as rollback:
            container = QWidget(parent_widget)
            rollback.callback(container.deleteLater)
            lane_layout = create_lane_layout(container, len(media.lanes))
            if not media.lanes:
                lane_layout.addWidget(QLabel("No considered lanes in this entry"), 0, 0)
            playback = OfflinePlayback(media.video_sources, parent_widget)
            rollback.callback(playback.deleteLater)
            rollback.callback(playback.close)
            analysis_jobs = (jobs or background_jobs(_ANALYSIS_POOL)).scope(container)
            rollback.callback(analysis_jobs.close)
            lanes = [
                build_lane(
                    parent_widget,
                    opened,
                    position,
                    len(media.lanes),
                    container,
                    lane_layout,
                    opened_source=media.video_sources[opened.source_index],
                    analysis_jobs=analysis_jobs,
                    render_catalog_manager=render_catalog_manager,
                    use_lane_controls=use_lane_controls and len(media.lanes) == 1,
                    lane_visibility={} if lane_visibility is None else lane_visibility,
                    pane_title=pane_title,
                    rollback=rollback,
                )
                for position, opened in enumerate(media.lanes)
            ]
            runtime = cls(
                lanes, container, media=media, playback=playback, analysis_jobs=analysis_jobs, parent=parent_widget
            )
            rollback.callback(runtime.deleteLater)
            runtime._connect_viewport_sync()
            runtime._connect_export_actions()
            runtime._connect_frame_requests()
            rollback.pop_all()
        return runtime

    def _connect_viewport_sync(self) -> None:
        if len(self.lanes) < 2:
            return
        for source_lane in self.lanes:
            peers = [lane.display for lane in self.lanes if lane is not source_lane]
            source_lane.display.viewportChanged.connect(
                lambda viewport, _peers=peers: self._apply_viewport_to_peers(viewport, _peers)
            )

    def _connect_export_actions(self) -> None:
        """Export a lane when its export action is triggered, from its media tools button or its context menu."""
        for lane in self.lanes:
            lane.export_action.triggered.connect(lambda _checked=False, lane=lane: self.export_lanes([lane]))

    def _connect_frame_requests(self) -> None:
        """Jump to frames requested from any lane's events or object history."""
        for lane in self.lanes:
            lane.tools_panel.frameRequested.connect(self.playback.seek)

    def export_lanes(self, lanes: list[OfflineLane] | None = None) -> None:
        """Export the given lanes, or let the user choose among all lanes when none are given."""
        selected = [lane for lane in (self.lanes if lanes is None else lanes) if lane in self.lanes]
        run_lane_export(
            self.container, selected, pause_playback=self.playback.pause, set_export_allowed=self._set_export_allowed
        )

    def _set_export_allowed(self, allowed: bool) -> None:
        """Allow or forbid export from every lane."""
        for lane in self.lanes:
            lane.set_export_allowed(allowed)

    def _apply_viewport_to_peers(
        self,
        viewport: NormalizedViewport,
        peers: list[FrameDisplay],
    ) -> None:
        for peer in peers:
            peer.set_viewport(viewport)

    def iter_video_sources(self) -> list[FileFrameSource]:
        """Return the sources owned by this entry's media."""
        return self._media.video_sources.copy()

    def cleanup(self) -> None:
        """Tear down the displays and hand the media to the background release owner."""
        self.playback.close()

        for lane in self.lanes:
            lane.cleanup_display()
        self._analysis_jobs.close()
        self.lanes.clear()

        self.container.setParent(None)
        # Offline rebuilds replace the viewer synchronously; flush deferred deletion so
        # old renderer widgets are gone before the next runtime is installed.
        self.container.deleteLater()
        QCoreApplication.sendPostedEvents(self.container, QEvent.Type.DeferredDelete)

        release_media(self._media)
        self.deleteLater()
