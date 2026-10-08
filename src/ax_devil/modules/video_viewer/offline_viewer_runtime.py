"""Runtime orchestration for offline video viewer entries."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import partial

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ax_devil.core.data_types import FrameData
from ax_devil.core.playback_speed import DEFAULT_PLAYBACK_SPEED, clamp_playback_speed
from ax_devil.modules.chrome.tokens import Radius, Space
from ax_devil.modules.data_sources import FileFrameSource, FileOverlaySource
from ax_devil.modules.data_sources.timing_reports import OverlayAlignmentReport
from ax_devil.modules.scene.rendering import (
    OverlayVisibility,
    SceneRenderCatalogManager,
    SceneRenderCatalogSelection,
)
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.synchronization import TimestampFallbackPolicy
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.viewport_state import NormalizedViewport, ZoomStep
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel, format_timecode
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.overlay_layout import OverlayPosition
from ax_devil.modules.video_viewer.lane_grid import lane_grid_columns, lane_grid_rows
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_entry_media import (
    EntryMedia,
    OpenedLane,
    place_scene_history,
    release_media,
)
from ax_devil.modules.video_viewer.overlay_persistence import (
    OverlayPersistencePolicy,
    OverlayPersistenceSettings,
)
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter
from ax_devil.modules.video_viewer.scene_inspection import schedule_scene_inspection_update
from ax_devil.modules.video_viewer.timing_diagnostics_widget import (
    OverlayAlignmentIndicator,
    TimingDiagnosticsWidget,
)
from ax_devil.modules.workspace import EntryLane, SeekableVideoContent


def show_video_on_controls(controls: SeekableVideoControlPanel, source: FileFrameSource) -> None:
    """Let *controls* follow *source*'s frame count, frame rate and cached frames."""
    controls.show_video(
        source.get_total_frames(),
        float(source.fps),
        source.get_cached_ranges,
        source.get_duration_s(),
        source.peek_frame_seconds,
    )


def video_details(source: FileFrameSource) -> str:
    """Return *source*'s size, frame rate and length for the pane header, such as ``1920×1080 · 30 fps · 0:20``."""
    parts = []
    size = source.get_frame_size()
    if size is not None:
        parts.append(f"{size[0]}×{size[1]}")
    fps = float(source.fps)
    if fps > 0:
        parts.append(f"{round(fps, 2):g} fps")
        length = source.get_duration_s()
        parts.append(
            format_timecode(length if length is not None else source.get_total_frames() / fps, hundredths=False)
        )
    return " · ".join(parts)


class _FrameDeliveryRelay(QObject):
    """Hand frames to the GUI thread, presenting only the newest one when deliveries queue up.

    Deliveries arrive as queued GUI-thread events. The first one posts a flush behind any deliveries already
    queued, so a GUI thread that falls behind skips superseded frames before overlay lookup and presentation.
    """

    frameReady = Signal(int, FrameData)
    _flushRequested = Signal()

    def __init__(self, source_index: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source_index = source_index
        self._pending: FrameData | None = None
        self._flushRequested.connect(self._flush, Qt.ConnectionType.QueuedConnection)

    @Slot(FrameData)
    def deliver(self, frame_data: FrameData) -> None:
        """Keep *frame_data* as the newest frame and schedule one flush for it."""
        first = self._pending is None
        self._pending = frame_data
        if first:
            self._flushRequested.emit()

    @Slot()
    def _flush(self) -> None:
        frame_data, self._pending = self._pending, None
        if frame_data is not None:
            self.frameReady.emit(self._source_index, frame_data)


class _SecondaryFrameRelay(QObject):
    """Marshal secondary-frame callbacks back onto the GUI thread."""

    frameReady = Signal(int, int, object)


@dataclass(slots=True)
class OfflineLane:
    """Per-lane runtime state for one offline display lane.

    Uses a `FrameDisplay` instance for rendering and related lane-owned helpers.
    """

    content: SeekableVideoContent
    name: str
    display: FrameDisplay
    overlay_source: FileOverlaySource | None
    controls: SeekableVideoControlPanel | None = None
    overlay_policy: OverlayPersistencePolicy | None = None
    presenter: SceneFramePresenter | None = None
    tools_panel: MediaToolsPanel | None = None
    timing_controls: TimingDiagnosticsWidget | None = None
    alignment_indicator: OverlayAlignmentIndicator | None = None
    video_source: FileFrameSource | None = None
    source_index: int = 0
    _pending_frame_index: int | None = field(default=None, init=False, repr=False)
    _desired_frame_index: int | None = field(default=None, init=False, repr=False)

    def display_frame(self, frame: VideoFrameWithOverlays) -> None:
        """Display a synced frame and update lane-owned controls."""
        self.display.display_frame(frame)
        if self.controls is not None and frame.frame.frame_id is not None:
            self.controls.set_current_frame(frame.frame.frame_id, frame.frame.timestamp)

    def present_frame(self, frame_data: FrameData) -> None:
        """Present one offline frame using direct overlay lookup."""
        overlay_data = None
        overlay_metadata: dict[str, float | bool] | None = None
        if self.overlay_source is not None:
            if self.overlay_policy is not None:
                selection = self.overlay_policy.select_from_source(self.overlay_source, frame_data.frame_id)
                overlay_data = selection.overlay
                overlay_metadata = selection.to_metadata()
            else:
                overlay_data = self.overlay_source.get_overlay_at_frame(frame_data.frame_id)

        presenter = self.presenter or SceneFramePresenter()
        presentation = presenter.prepare_frame(frame_data, overlay_data, overlay_metadata=overlay_metadata)
        self.display_frame(presentation.display_frame)
        schedule_scene_inspection_update(self.tools_panel, presentation.inspection)

    def show_video_on_controls(self, source: FileFrameSource) -> None:
        """Let the lane controls follow *source*'s frames, time and cache."""
        if self.controls is not None:
            show_video_on_controls(self.controls, source)

    def sync_playback_state(self, playing: bool) -> None:
        """Synchronize caller-owned controls with the runtime playback state."""
        if self.controls is not None:
            self.controls.sync_playback_state(playing)

    def set_playback_speed(self, speed: float, *, emit_signals: bool = False) -> None:
        """Set the lane control playback speed."""
        if self.controls is not None:
            self.controls.set_playback_speed(speed, emit_signals=emit_signals)

    def initialize_timing_diagnostics(self, alignment_report: OverlayAlignmentReport | None) -> None:
        """Populate timing controls and alignment status from this lane's sources and opening-time report."""
        if self.timing_controls is None or self.video_source is None:
            return
        self.timing_controls.update_timing_profile(self.video_source.get_timing_profile())
        self._show_alignment_report(alignment_report)
        if self.overlay_source is None:
            self.timing_controls.set_timestamp_fallback_controls_enabled(False)
            return
        self.timing_controls.set_timestamp_fallback_controls_enabled(True)
        self.timing_controls.set_timestamp_fallback_policy(self.overlay_source.get_timestamp_fallback_policy())

    def connect_timestamp_fallback_policy_changes(
        self,
        on_changed: Callable[[TimestampFallbackPolicy], None],
    ) -> None:
        """Connect timestamp fallback policy changes for this lane."""
        if self.overlay_source is not None and self.timing_controls is not None:
            self.timing_controls.timestampFallbackPolicyChanged.connect(on_changed)

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        """Apply a timestamp fallback policy and refresh alignment status."""
        if self.overlay_source is None:
            return
        self.overlay_source.set_timestamp_fallback_policy(policy)
        self.refresh_alignment_report()
        self.refresh_scene_history()

    def refresh_scene_history(self) -> None:
        """Place the overlay history again after a change in how lookup selects samples."""
        if self.tools_panel is None or self.video_source is None:
            return
        history = place_scene_history(self.overlay_source, self.overlay_policy, self.video_source.get_frame_timeline())
        if history is not None:
            self.tools_panel.set_scene_history(history)

    def refresh_alignment_report(self) -> None:
        """Refresh the visible overlay alignment report."""
        report: OverlayAlignmentReport | None = None
        if self.overlay_source is not None and self.video_source is not None:
            report = self.overlay_source.analyze_alignment(self.video_source.get_frame_timeline())
        self._show_alignment_report(report)

    def _show_alignment_report(self, report: OverlayAlignmentReport | None) -> None:
        if self.timing_controls is None or self.alignment_indicator is None:
            return
        self.timing_controls.update_alignment_report(report)
        self.alignment_indicator.update_alignment_report(report)

    def reset_frame_requests(self) -> None:
        """Clear outstanding async frame request bookkeeping."""
        self._pending_frame_index = None
        self._desired_frame_index = None

    def request_frame(
        self,
        frame_number: int,
        callback: Callable[[int, FrameData | None], None],
    ) -> None:
        """Request and eventually present a frame from this lane's non-primary source."""
        if self.video_source is None:
            return
        self._desired_frame_index = frame_number
        if self._pending_frame_index is None:
            self._request_desired_frame(callback)

    def complete_frame_request(
        self,
        requested_frame: int,
        frame_data: FrameData | None,
        callback: Callable[[int, FrameData | None], None],
    ) -> None:
        """Present an async response if current, or request the latest desired frame."""
        if self._pending_frame_index != requested_frame:
            return
        self._pending_frame_index = None
        latest_frame = self._desired_frame_index
        if latest_frame is not None and latest_frame != requested_frame:
            self._request_desired_frame(callback)
            return
        if frame_data is not None:
            self.present_frame(frame_data)

    def cleanup_display(self) -> None:
        """Release display-owned mount widgets before the lane container is deleted."""
        if self.presenter is not None:
            self.presenter.clear()
        if self.tools_panel is not None:
            self.tools_panel.cleanup()
        self.display.cleanup()

    def _request_desired_frame(self, callback: Callable[[int, FrameData | None], None]) -> None:
        if self.video_source is None or self._desired_frame_index is None:
            return
        frame_number = self._desired_frame_index
        self._pending_frame_index = frame_number
        self.video_source.request_frame_async(
            frame_number,
            lambda frame_data, requested_frame=frame_number: callback(requested_frame, frame_data),
        )


@dataclass(slots=True)
class _PooledVideoSource:
    """One source-pool entry shared by lanes that reference the same seekable video."""

    source: FileFrameSource
    relay: _FrameDeliveryRelay
    source_index: int


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
        source_pool: list[_PooledVideoSource],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.lanes = lanes
        self.container = container
        self._media = media
        self._source_pool = source_pool
        self._is_playing = False
        self._current_frame_seconds: float | None = None
        self._min_valid_primary_generation: int | None = None
        self._secondary_frame_relay = _SecondaryFrameRelay(self)
        self._secondary_frame_relay.frameReady.connect(self._display_secondary_frame)

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
    def current_frame_seconds(self) -> float | None:
        """Return the decoded time of the primary frame on screen, or None before the first frame."""
        return self._current_frame_seconds

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
            lane_layout = cls._create_lane_layout(container, len(media.lanes))
            if not media.lanes:
                lane_layout.addWidget(QLabel("No considered lanes in this entry"))
            source_pool = [
                _PooledVideoSource(
                    source=source,
                    relay=_FrameDeliveryRelay(source_index, parent_widget),
                    source_index=source_index,
                )
                for source_index, source in enumerate(media.video_sources)
            ]
            for pooled_source in source_pool:
                rollback.callback(pooled_source.relay.deleteLater)
            lanes = [
                cls._build_lane(
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
            runtime._connect_export_signals()
            runtime._connect_frame_requests()
            runtime._connect_timestamp_fallback_controls()
            rollback.pop_all()
        return runtime

    @staticmethod
    def _create_lane_layout(container: QWidget, lane_count: int) -> QHBoxLayout | QGridLayout:
        if lane_count <= 2:
            h_layout = QHBoxLayout(container)
            h_layout.setContentsMargins(0, 0, 0, 0)
            h_layout.setSpacing(Space.XS)
            return h_layout

        grid_layout = QGridLayout(container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(Space.XS)
        grid_layout.setVerticalSpacing(Space.XS)

        columns = lane_grid_columns(lane_count)
        rows = lane_grid_rows(lane_count)
        for column in range(columns):
            grid_layout.setColumnStretch(column, 1)
        for row in range(rows):
            grid_layout.setRowStretch(row, 1)
        return grid_layout

    @staticmethod
    def _add_lane_display(
        parent_widget: QWidget,
        lane_layout: QHBoxLayout | QGridLayout,
        lane_count: int,
        lane_index: int,
        lane: EntryLane,
        display: FrameDisplay,
        pane_title: str,
    ) -> OverlayAlignmentIndicator:
        pane = QWidget(parent_widget)
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(0)

        lane_name = lane.display_name or lane.video.display_name
        if lane_count > 1 or lane_name != pane_title:
            name_label = QLabel(lane_name, display.viewport)
            name_label.setObjectName("lane-indicator-label")
            name_label.setStyleSheet(
                f"background-color: rgba(0, 0, 0, 160); border-radius: {Radius.CONTROL}px; color: white;"
                f"padding: {Space.XS}px {Space.M}px;"
            )
            display.mount_overlay(
                name_label,
                position=OverlayPosition.FRAME_TOP_LEFT,
                hide_while_inspecting=True,
                preference=OverlayPreference.LANE_NAMES,
            )
        alignment_indicator = OverlayAlignmentIndicator(display.viewport)
        display.mount_overlay(alignment_indicator, position=OverlayPosition.FRAME_TOP_RIGHT)
        pane_layout.addWidget(display, 1)

        if isinstance(lane_layout, QGridLayout):
            columns = lane_grid_columns(lane_count)
            row, column = divmod(lane_index, columns)
            lane_layout.addWidget(pane, row, column)
            return alignment_indicator
        lane_layout.addWidget(pane)
        return alignment_indicator

    @classmethod
    def _build_lane(
        cls,
        parent_widget: QWidget,
        opened: OpenedLane,
        position: int,
        lane_count: int,
        container: QWidget,
        lane_layout: QHBoxLayout | QGridLayout,
        *,
        source_pool: list[_PooledVideoSource],
        render_catalog_manager: SceneRenderCatalogManager,
        use_lane_controls: bool,
        lane_visibility: dict[int, OverlayVisibility],
        pane_title: str,
        rollback: ExitStack,
    ) -> OfflineLane:
        lane_content = opened.lane
        video = opened.video
        display = FrameDisplay()
        rollback.callback(display.cleanup)
        display.viewport.set_diagnostics_label(f"{video.display_name} · {lane_content.display_name}")
        controls = SeekableVideoControlPanel(display.viewport) if use_lane_controls else None
        if controls is not None:
            display.mount_overlay(controls, position=OverlayPosition.BOTTOM_FULL_WIDTH)
        display.enable_side_panel()
        alignment_indicator = cls._add_lane_display(
            parent_widget, lane_layout, lane_count, position, lane_content, display, pane_title
        )

        pooled_source = source_pool[opened.source_index]
        overlay_source = opened.overlay_source
        overlay_policy = opened.overlay_policy
        display.viewport.set_diagnostics_sources((overlay_source.diagnostics_id,) if overlay_source else ())
        timing_controls = TimingDiagnosticsWidget()
        render_catalog_selection = render_catalog_manager.create_selection(
            visibility=lane_visibility.get(opened.lane_index, OverlayVisibility()), parent=container
        )

        def remember_visibility(
            *, selection: SceneRenderCatalogSelection = render_catalog_selection, lane_index: int = opened.lane_index
        ) -> None:
            lane_visibility[lane_index] = selection.visibility

        render_catalog_selection.selectionChanged.connect(remember_visibility)
        tools_panel = MediaToolsPanel(
            render_catalog_selection,
            filter_config=overlay_source.get_filter_config() if overlay_source is not None else None,
            overlay_settings=OverlayPersistenceSettings.default_enabled(),
            show_export=True,
            scene_history=opened.scene_history,
            timing_controls=timing_controls,
        )
        presenter = SceneFramePresenter(
            filter_widget=tools_panel.filter_widget,
            scene_render_catalog=render_catalog_selection.active_catalog(),
        )

        def refresh_display_overlays(_catalog: object, *, lane_display: FrameDisplay = display) -> None:
            lane_display.refresh_overlays()

        display.set_side_panel_widget(tools_panel)
        tools_panel.filter_widget.filterChanged.connect(display.refresh_overlays)
        render_catalog_selection.activeCatalogChanged.connect(presenter.attach_scene_render_catalog)
        render_catalog_selection.activeCatalogChanged.connect(refresh_display_overlays)
        tools_panel.catalogViewerRequested.connect(
            partial(cls._open_catalog_viewer, container, render_catalog_selection)
        )
        if overlay_policy is not None:
            tools_panel.overlayPersistenceChanged.connect(overlay_policy.update_settings)

        lane = OfflineLane(
            content=video,
            name=lane_content.display_name,
            display=display,
            overlay_source=overlay_source,
            controls=controls,
            overlay_policy=overlay_policy,
            presenter=presenter,
            tools_panel=tools_panel,
            timing_controls=timing_controls,
            alignment_indicator=alignment_indicator,
            video_source=pooled_source.source,
            source_index=pooled_source.source_index,
        )
        lane.initialize_timing_diagnostics(opened.alignment_report)
        return lane

    def _connect_viewport_sync(self) -> None:
        if len(self.lanes) < 2:
            return
        for source_lane in self.lanes:
            peers = [lane.display for lane in self.lanes if lane is not source_lane]
            source_lane.display.viewportChanged.connect(
                lambda viewport, _peers=peers: self._apply_viewport_to_peers(viewport, _peers)
            )

    def _connect_export_signals(self) -> None:
        """Wire export button signals from each lane's tools panel."""
        for lane in self.lanes:
            if lane.tools_panel is not None:
                lane.tools_panel.exportRequested.connect(partial(self.export_lanes, [lane]))

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
        from ax_devil.modules.video_viewer.export import ExportDialog, ExportLane
        from ax_devil.modules.video_viewer.export.frozen_scene_filter import FrozenSceneFilter

        lanes = [lane for lane in (self.lanes if lanes is None else lanes) if lane.video_source is not None]
        if not lanes:
            return

        # Pause playback so the export has exclusive access to the decoder
        self.pause_playback()

        # Disable export buttons to prevent re-entrant launches
        self._set_export_buttons_enabled(False)

        export_lanes: list[ExportLane] = []
        for lane in lanes:
            assert lane.video_source is not None
            # Snapshot filter state so it can't change during export
            filter_widget = lane.tools_panel.filter_widget if lane.tools_panel is not None else None
            frozen_filter: FrozenSceneFilter | None = None
            if filter_widget is not None:
                frozen_filter = FrozenSceneFilter(filter_widget.filter_config, filter_widget.filter_state)

            active_catalog = lane.presenter.scene_render_catalog if lane.presenter is not None else None
            # Export uses the same timestamp selection settings as the viewer.
            export_lanes.append(
                ExportLane(
                    name=lane.name or lane.content.display_name,
                    video_source=lane.video_source,
                    presenter=SceneFramePresenter(filter_widget=frozen_filter, scene_render_catalog=active_catalog),
                    overlay_source=lane.overlay_source,
                    overlay_policy=(
                        OverlayPersistencePolicy(lane.overlay_policy.settings.copy())
                        if lane.overlay_policy is not None
                        else None
                    ),
                )
            )

        try:
            with ExportDialog(export_lanes, parent=self.container) as dialog:
                dialog.exec()
        finally:
            self._set_export_buttons_enabled(True)
        # Playback intentionally stays paused — user resumes manually

    def _set_export_buttons_enabled(self, enabled: bool) -> None:
        """Enable or disable all lane export buttons."""
        for lane in self.lanes:
            if lane.tools_panel is not None:
                lane.tools_panel.set_export_enabled(enabled)

    def _apply_viewport_to_peers(
        self,
        viewport: NormalizedViewport,
        peers: list[FrameDisplay],
    ) -> None:
        for peer in peers:
            peer.set_viewport(viewport)

    @staticmethod
    def _open_catalog_viewer(parent: QWidget, render_catalog_selection: SceneRenderCatalogSelection) -> None:
        from ax_devil.modules.catalog_viewer import show_catalog_viewer

        show_catalog_viewer(
            render_catalog_selection.manager,
            parent=parent,
            catalog_path=render_catalog_selection.active_catalog_path(),
        )

    def start_playback(self) -> None:
        """Start playback for the active runtime."""
        primary_source = self.get_primary_video_source()
        if primary_source is None:
            return

        total_frames = self.total_frames
        at_end_of_media = total_frames > 0 and primary_source.get_current_frame() >= total_frames - 1

        for lane in self.lanes:
            lane.reset_frame_requests()

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
        for lane in self.lanes:
            lane.reset_frame_requests()
            lane.sync_playback_state(False)

    def jump_to_frame(self, frame_num: int) -> None:
        """Jump the primary source to a specific frame."""
        primary_source = self.get_primary_video_source()
        if primary_source is None:
            return

        for lane in self.lanes:
            lane.reset_frame_requests()

        target = self._bounded_frame(frame_num)
        self._current_frame = target
        primary_source.jump_to(target)
        self._update_min_valid_primary_generation()

    def step_frames(self, delta: int) -> None:
        """Step the primary source by *delta* frames."""
        primary_source = self.get_primary_video_source()
        if primary_source is None:
            return

        for lane in self.lanes:
            lane.reset_frame_requests()

        target = self._bounded_frame(self.current_frame + delta)
        self._current_frame = target
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

    def cleanup(self, *, blocking: bool = False) -> None:
        """Tear down the displays and release the media.

        By default the sources close on a worker thread; *blocking* closes them before returning, for a viewer that
        is going away and must not leave source workers running.
        """
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
        self.lanes.clear()

        self.container.setParent(None)
        # Offline rebuilds replace the viewer synchronously; flush deferred deletion so
        # old renderer widgets are gone before the next runtime is installed.
        self.container.deleteLater()
        QCoreApplication.sendPostedEvents(self.container, QEvent.Type.DeferredDelete)

        if blocking:
            self._media.close()
            self._media.delete_later()
        else:
            release_media(self._media)
        self.deleteLater()

    def _on_frame_ready(self, source_index: int, frame_data: FrameData) -> None:
        if source_index == 0 and self._is_stale_primary_frame(frame_data):
            return

        if source_index == 0:
            self._current_frame = self._bounded_frame(frame_data.frame_id.sequence_id)
            self._current_frame_seconds = frame_data.frame_id.timestamp_monotime_us / 1_000_000
            self.currentFrameChanged.emit(self.current_frame)
        for lane in self.lanes:
            if lane.source_index == source_index:
                lane.present_frame(frame_data)
        if source_index == 0:
            self._sync_secondary_video_positions(self.current_frame)

    def _display_secondary_frame(self, lane_index: int, requested_frame: int, frame_data: FrameData | None) -> None:
        if not (0 <= lane_index < len(self.lanes)):
            return
        lane = self.lanes[lane_index]
        lane.complete_frame_request(
            requested_frame,
            frame_data,
            lambda next_requested_frame, next_frame_data, idx=lane_index: self._secondary_frame_relay.frameReady.emit(
                idx,
                next_requested_frame,
                next_frame_data,
            ),
        )

    def _sync_secondary_video_positions(self, target_frame: int) -> None:
        for index, lane in enumerate(self.lanes[1:], start=1):
            if lane.source_index == 0:
                continue
            lane.request_frame(
                target_frame,
                lambda requested_frame, frame_data, idx=index: self._secondary_frame_relay.frameReady.emit(
                    idx,
                    requested_frame,
                    frame_data,
                ),
            )

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
