"""Runtime orchestration for offline video viewer entries."""

from __future__ import annotations

from contextlib import ExitStack
from functools import partial

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ax_devil.core.data_types import FrameData
from ax_devil.core.playback_speed import DEFAULT_PLAYBACK_SPEED, clamp_playback_speed
from ax_devil.core.time_units import us_to_seconds
from ax_devil.modules.background_jobs import BackgroundJobs, background_jobs
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.scene.rendering import (
    OverlayVisibility,
    SceneRenderCatalogManager,
)
from ax_devil.modules.synchronization import TimestampFallbackPolicy
from ax_devil.modules.video_player.engine.viewport_state import NormalizedViewport, ZoomStep
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.offline_entry_media import (
    EntryMedia,
    release_media,
)
from ax_devil.modules.video_viewer.offline_export import run_lane_export
from ax_devil.modules.video_viewer.offline_frame_pool import (
    FrameDeliveryRelay,
    PooledVideoSource,
    PresentedFrame,
    SecondaryFrameRelay,
)
from ax_devil.modules.video_viewer.offline_lane import OfflineLane
from ax_devil.modules.video_viewer.offline_lane_builder import (
    build_lane,
    create_lane_layout,
)

# Whole-file analysis of a lane's overlay; its own pool keeps it from delaying the shared one.
_ANALYSIS_POOL = "overlay-analysis"


class OfflineSession(QObject):
    """One active offline entry runtime: frame displays built over already opened media, which it owns."""

    currentFrameChanged = Signal(int)
    playbackStateChanged = Signal(bool)
    playbackFinished = Signal()

    def __init__(
        self,
        lanes: list[OfflineLane],
        container: QWidget,
        *,
        media: EntryMedia,
        source_pool: list[PooledVideoSource],
        jobs: BackgroundJobs | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.lanes = lanes
        self.container = container
        self._media = media
        self._source_pool = source_pool
        self._is_playing = False
        self._presented_frame: PresentedFrame | None = None
        self._min_valid_primary_generation: int | None = None
        self._secondary_frame_relay = SecondaryFrameRelay(self)
        self._secondary_frame_relay.frameReady.connect(self._display_secondary_frame)
        self._analysis_jobs = (jobs or background_jobs(_ANALYSIS_POOL)).scope(self)
        for lane in lanes:
            lane.analysis_jobs = self._analysis_jobs

        primary_source = self.get_primary_video_source()
        self._current_frame = (
            self._bounded_frame(primary_source.get_current_frame()) if primary_source is not None else 0
        )
        if primary_source is not None:
            primary_source.sourceFinished.connect(self._on_video_finished)
            primary_lane = self.primary_lane
            if primary_lane is not None:
                primary_lane.show_video_on_controls(primary_source)
        self.set_playback_speed(DEFAULT_PLAYBACK_SPEED)

    @property
    def primary_lane(self) -> OfflineLane | None:
        """Return the primary lane for the runtime."""
        if not self.lanes:
            return None
        return self.lanes[0]

    @property
    def primary_controls(self) -> SeekableVideoControlPanel | None:
        """Return the primary lane controls, if that lane owns controls."""
        primary_lane = self.primary_lane
        if primary_lane is None:
            return None
        return primary_lane.controls

    @property
    def displays(self) -> tuple[FrameDisplay, ...]:
        """Return all active lane displays in presentation order."""
        return tuple(lane.display for lane in self.lanes)

    def focus_lane(self, lane_index: int) -> bool:
        """Focus the lane with this original entry index, returning whether it is currently shown."""
        lane = next((lane for lane in self.lanes if lane.lane_index == lane_index), None)
        if lane is None:
            return False
        lane.display.viewport.setFocus()
        return True

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
    def total_frames(self) -> int:
        """Return total frame count for the primary source."""
        primary_source = self.get_primary_video_source()
        return primary_source.get_total_frames() if primary_source is not None else 0

    @property
    def current_frame(self) -> int:
        """Return the current primary frame index."""
        return self._current_frame

    @property
    def presented_frame(self) -> PresentedFrame | None:
        """Return the primary frame on screen, or None before the first frame.

        Unlike ``current_frame``, which moves to a seek target at once, it stays the frame last shown until the
        requested one arrives.
        """
        return self._presented_frame

    @property
    def is_playing(self) -> bool:
        """Return whether primary playback is active."""
        return self._is_playing

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
            source_pool = [
                PooledVideoSource(
                    source=source,
                    relay=FrameDeliveryRelay(source_index, parent_widget),
                    source_index=source_index,
                )
                for source_index, source in enumerate(media.video_sources)
            ]
            for pooled_source in source_pool:
                rollback.callback(pooled_source.relay.deleteLater)
            lanes = [
                build_lane(
                    parent_widget,
                    opened,
                    position,
                    len(media.lanes),
                    container,
                    lane_layout,
                    source_pool=source_pool,
                    render_catalog_manager=render_catalog_manager,
                    use_lane_controls=use_lane_controls and len(media.lanes) == 1,
                    lane_visibility={} if lane_visibility is None else lane_visibility,
                    pane_title=pane_title,
                    rollback=rollback,
                )
                for position, opened in enumerate(media.lanes)
            ]
            runtime = cls(lanes, container, media=media, source_pool=source_pool, parent=parent_widget)
            rollback.callback(runtime.deleteLater)
            for pooled_source in source_pool:
                pooled_source.relay.frameReady.connect(runtime._on_frame_ready)
                pooled_source.source.frameReady.connect(pooled_source.relay.deliver)
                rollback.callback(pooled_source.source.frameReady.disconnect, pooled_source.relay.deliver)
            runtime._connect_viewport_sync()
            runtime._connect_export_actions()
            runtime._connect_frame_requests()
            runtime._connect_timestamp_fallback_controls()
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
            if lane.tools_panel is not None:
                lane.tools_panel.frameRequested.connect(self.jump_to_frame)
                # Connected after the lane policy, so the history follows the settings just applied.
                lane.tools_panel.overlayPersistenceChanged.connect(
                    lambda _settings, lane=lane: lane.refresh_scene_history()
                )

    def _connect_timestamp_fallback_controls(self) -> None:
        """Wire timestamp fallback mode controls from each lane diagnostics widget."""
        for lane in self.lanes:
            lane.connect_timestamp_fallback_policy_changes(partial(self._set_lane_timestamp_fallback_policy, lane))

    def _set_lane_timestamp_fallback_policy(self, lane: OfflineLane, policy: TimestampFallbackPolicy) -> None:
        """Update timestamp fallback policy for one lane and refresh its visible state."""
        lane.set_timestamp_fallback_policy(policy)
        self.jump_to_frame(self.current_frame)

    def export_lanes(self, lanes: list[OfflineLane] | None = None) -> None:
        """Export the given lanes, or let the user choose among all lanes when none are given."""
        selected = [
            lane
            for lane in (self.lanes if lanes is None else lanes)
            if lane in self.lanes and lane.video_source is not None
        ]
        run_lane_export(
            self.container, selected, pause_playback=self.pause_playback, set_export_allowed=self._set_export_allowed
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

    def start_playback(self) -> None:
        """Start playback for the active runtime."""
        primary_source = self.get_primary_video_source()
        if primary_source is None:
            return

        total_frames = self.total_frames
        at_end_of_media = total_frames > 0 and primary_source.get_current_frame() >= total_frames - 1

        for pooled_source in self._source_pool[1:]:
            pooled_source.reset_frame_requests()

        if at_end_of_media:
            primary_source.reset_to_start()
            self._current_frame = 0
        playing = primary_source.play()
        self._is_playing = playing
        self._update_min_valid_primary_generation()
        self.playbackStateChanged.emit(playing)
        for lane in self.lanes:
            lane.sync_playback_state(playing)

    def pause_playback(self) -> None:
        """Pause playback for the active runtime."""
        primary_source = self.get_primary_video_source()
        if primary_source is None:
            return

        primary_source.pause()
        self._is_playing = False
        self.playbackStateChanged.emit(False)
        for pooled_source in self._source_pool[1:]:
            pooled_source.reset_frame_requests()
        for lane in self.lanes:
            lane.sync_playback_state(False)

    def jump_to_frame(self, frame_num: int) -> None:
        """Jump the primary source to a specific frame."""
        primary_source = self.get_primary_video_source()
        if primary_source is None:
            return

        for pooled_source in self._source_pool[1:]:
            pooled_source.reset_frame_requests()

        target = self._bounded_frame(frame_num)
        self._current_frame = target
        for lane in self.lanes:
            if lane.tools_panel is not None:
                lane.tools_panel.refresh_scene()
        primary_source.jump_to(target)
        self._update_min_valid_primary_generation()

    def step_frames(self, delta: int) -> None:
        """Step the primary source by *delta* frames."""
        primary_source = self.get_primary_video_source()
        if primary_source is None:
            return

        for pooled_source in self._source_pool[1:]:
            pooled_source.reset_frame_requests()

        target = self._bounded_frame(self.current_frame + delta)
        self._current_frame = target
        for lane in self.lanes:
            if lane.tools_panel is not None:
                lane.tools_panel.refresh_scene()
        primary_source.jump_to(target)
        self._update_min_valid_primary_generation()

    def get_primary_video_source(self) -> FileFrameSource | None:
        """Return the primary video source for the active runtime."""
        if not self._source_pool:
            return None
        return self._source_pool[0].source

    def iter_video_sources(self) -> list[FileFrameSource]:
        """Return all video sources used by the active runtime."""
        return [pooled_source.source for pooled_source in self._source_pool]

    def video_source_count(self) -> int:
        """Return the number of unique frame sources owned by the runtime."""
        return len(self.iter_video_sources())

    def set_playback_speed(self, speed: float) -> None:
        """Apply playback speed to all active video sources."""
        playback_speed = clamp_playback_speed(speed)
        for source in self.iter_video_sources():
            source.set_playback_speed(playback_speed)

    def cleanup(self) -> None:
        """Tear down the displays and hand the media to the background release owner."""
        self._is_playing = False
        primary_source = self.get_primary_video_source()
        if primary_source is not None:
            primary_source.sourceFinished.disconnect(self._on_video_finished)
        self._secondary_frame_relay.frameReady.disconnect(self._display_secondary_frame)

        for pooled_source in self._source_pool:
            pooled_source.source.frameReady.disconnect(pooled_source.relay.deliver)
            pooled_source.relay.frameReady.disconnect(self._on_frame_ready)
            pooled_source.relay.deleteLater()
        self._source_pool.clear()

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

    def _on_frame_ready(self, source_index: int, frame_data: FrameData) -> None:
        if source_index == 0 and self._is_stale_primary_frame(frame_data):
            return

        if source_index == 0:
            self._current_frame = self._bounded_frame(frame_data.frame_id.sequence_id)
            self._presented_frame = PresentedFrame(self._current_frame, us_to_seconds(frame_data.frame_id.timestamp_us))
            self.currentFrameChanged.emit(self.current_frame)
        for lane in self.lanes:
            if lane.source_index == source_index:
                lane.present_frame(frame_data)
        if source_index == 0:
            self._sync_secondary_video_positions(self.current_frame)

    def _display_secondary_frame(
        self, pooled_source: PooledVideoSource, generation: int, requested_frame: int, frame_data: FrameData | None
    ) -> None:
        if pooled_source not in self._source_pool:
            return
        if not pooled_source.complete_frame_request(generation, requested_frame, self._secondary_frame_relay):
            return
        if frame_data is not None:
            for lane in self.lanes:
                if lane.source_index == pooled_source.source_index:
                    lane.present_frame(frame_data)

    def _sync_secondary_video_positions(self, target_frame: int) -> None:
        for pooled_source in self._source_pool[1:]:
            pooled_source.request_frame(target_frame, self._secondary_frame_relay)

    def _on_video_finished(self) -> None:
        self._is_playing = False
        self.playbackStateChanged.emit(False)
        self.playbackFinished.emit()
        for lane in self.lanes:
            lane.sync_playback_state(False)

    def _update_min_valid_primary_generation(self) -> None:
        primary_source = self.get_primary_video_source()
        if primary_source is None:
            return
        self._min_valid_primary_generation = primary_source.get_position_generation()

    def _is_stale_primary_frame(self, frame_data: FrameData) -> bool:
        if self._min_valid_primary_generation is None or frame_data.metadata is None:
            return False
        generation = frame_data.metadata.get("position_generation")
        return isinstance(generation, int) and generation < self._min_valid_primary_generation

    def _bounded_frame(self, frame_number: int) -> int:
        if self.total_frames <= 0:
            return 0
        return max(0, min(frame_number, self.total_frames - 1))
