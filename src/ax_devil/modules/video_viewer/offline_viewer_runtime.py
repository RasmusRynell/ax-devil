"""Runtime orchestration for offline video viewer entries."""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import partial
from typing import TypeVar

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ax_devil.core.data_types import FrameData
from ax_devil.modules.data_sources import FileFrameSource, FileOverlaySource
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.plugin_system import get_file_decoder_factory
from ax_devil.modules.scene.rendering import (
    OverlayVisibility,
    SceneRenderCatalogManager,
    SceneRenderCatalogSelection,
)
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.synchronization import TimestampFallbackPolicy
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.playback_speed import DEFAULT_PLAYBACK_SPEED, clamp_playback_speed
from ax_devil.modules.video_player.engine.viewport_state import NormalizedViewport, ZoomStep
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.overlay_layout import OverlayPosition
from ax_devil.modules.video_viewer.lane_grid import lane_grid_columns, lane_grid_rows
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
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
from ax_devil.modules.workspace import (
    EntryLane,
    FileOverlaySourceSpec,
    LiveVideoContent,
    OverlayContent,
    PlaylistEntry,
    SeekableVideoContent,
)

_T = TypeVar("_T")


class LoadingCancelled(Exception):
    """Raised when a background loading operation is cancelled during shutdown."""


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
            self.controls.set_current_frame(frame.frame.frame_id)

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

    def set_total_frames(self, total: int) -> None:
        """Update the lane controls with the source frame count."""
        if self.controls is not None:
            self.controls.set_total_frames(total)

    def sync_playback_state(self, playing: bool) -> None:
        """Synchronize caller-owned controls with the runtime playback state."""
        if self.controls is not None:
            self.controls.sync_playback_state(playing)

    def set_playback_speed(self, speed: float, *, emit_signals: bool = False) -> None:
        """Set the lane control playback speed."""
        if self.controls is not None:
            self.controls.set_playback_speed(speed, emit_signals=emit_signals)

    def initialize_timing_diagnostics(self) -> None:
        """Populate timing controls and alignment status from this lane's sources."""
        if self.timing_controls is None or self.video_source is None:
            return
        self.timing_controls.update_timing_profile(self.video_source.get_timing_profile())
        self.refresh_alignment_report()
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
        history = OfflineSession._scene_history(
            self.overlay_source, self.overlay_policy, self.video_source.get_frame_timeline()
        )
        if history is not None:
            self.tools_panel.set_scene_history(history)

    def refresh_alignment_report(self) -> None:
        """Refresh the visible overlay alignment report."""
        if self.timing_controls is None or self.alignment_indicator is None:
            return
        report: OverlayAlignmentReport | None = None
        if self.overlay_source is not None and self.video_source is not None:
            report = self.overlay_source.analyze_alignment(self.video_source.get_frame_timeline())
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

    video: SeekableVideoContent
    source: FileFrameSource
    relay: _FrameDeliveryRelay
    source_index: int


def _move_qobjects_to_thread(obj: object, thread: object) -> None:
    """Move any :class:`QObject` instances in *obj* to *thread*."""
    if isinstance(obj, QObject):
        obj.moveToThread(thread)  # type: ignore[arg-type]
    elif isinstance(obj, tuple):
        for item in obj:
            if isinstance(item, QObject):
                item.moveToThread(thread)  # type: ignore[arg-type]


class OfflineSession(QObject):
    """One active offline entry runtime, including frame displays and media sources."""

    currentFrameChanged = Signal(int)
    playbackStateChanged = Signal(bool)
    playbackFinished = Signal()

    def __init__(
        self,
        lanes: list[OfflineLane],
        container: QWidget,
        *,
        source_pool: list[_PooledVideoSource],
        cancel_loading: threading.Event | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.lanes = lanes
        self.container = container
        self._source_pool = source_pool
        self._cancel_loading = cancel_loading if cancel_loading is not None else threading.Event()
        self._is_playing = False
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
                primary_lane.set_total_frames(self.total_frames)
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
        entry: PlaylistEntry,
        *,
        lane_included: Callable[[int], bool],
        cancel_loading: threading.Event,
        on_status: Callable[[str], None] = lambda _: None,
        render_catalog_manager: SceneRenderCatalogManager,
        use_lane_controls: bool = True,
        lane_visibility: dict[int, OverlayVisibility] | None = None,
    ) -> "OfflineSession":
        """Build an offline runtime for one playlist entry.

        *lane_visibility* holds overlay visibility by original lane position; each lane starts from its entry and
        writes its changes back, so choices carry over to the next entry.
        """
        with ExitStack() as rollback:
            container = QWidget(parent_widget)
            rollback.callback(cls._delete_later_if_supported, container)
            lane_indices = tuple(lane_index for lane_index in range(len(entry.lanes)) if lane_included(lane_index))
            lanes = tuple(entry.lanes[lane_index] for lane_index in lane_indices)
            lane_layout = cls._create_lane_layout(container, len(lanes))
            if not lanes:
                lane_layout.addWidget(QLabel("No considered lanes in this entry"))
                runtime = cls(
                    [],
                    container,
                    source_pool=[],
                    cancel_loading=cancel_loading,
                    parent=parent_widget,
                )
                rollback.pop_all()
                return runtime
            runtime = cls._build_source_pooled_lanes(
                parent_widget,
                lanes,
                lane_indices,
                container,
                lane_layout,
                cancel_loading=cancel_loading,
                on_status=on_status,
                render_catalog_manager=render_catalog_manager,
                use_lane_controls=use_lane_controls,
                lane_visibility={} if lane_visibility is None else lane_visibility,
                rollback=rollback,
            )

            rollback.pop_all()
            return runtime

    @staticmethod
    def _create_lane_layout(container: QWidget, lane_count: int) -> QHBoxLayout | QGridLayout:
        if lane_count <= 2:
            h_layout = QHBoxLayout(container)
            h_layout.setContentsMargins(0, 0, 0, 0)
            h_layout.setSpacing(2)
            return h_layout

        grid_layout = QGridLayout(container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(2)
        grid_layout.setVerticalSpacing(2)

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
    ) -> OverlayAlignmentIndicator:
        pane = QWidget(parent_widget)
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(0)

        name_label = QLabel(lane.display_name or lane.video.display_name, display.viewport)
        name_label.setObjectName("lane-indicator-label")
        name_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 160); border-radius: 3px; color: white; padding: 2px 6px;"
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
    def _build_source_pooled_lanes(
        cls,
        parent_widget: QWidget,
        lane_contents: tuple[EntryLane, ...],
        lane_indices: tuple[int, ...],
        container: QWidget,
        lane_layout: QHBoxLayout | QGridLayout,
        *,
        cancel_loading: threading.Event,
        on_status: Callable[[str], None],
        render_catalog_manager: SceneRenderCatalogManager,
        use_lane_controls: bool,
        lane_visibility: dict[int, OverlayVisibility],
        rollback: ExitStack,
    ) -> "OfflineSession":
        lane_count = len(lane_contents)
        lanes: list[OfflineLane] = []
        source_pool: dict[str, _PooledVideoSource] = {}
        pooled_sources: list[_PooledVideoSource] = []

        def pooled_source_for(video: SeekableVideoContent) -> _PooledVideoSource:
            pooled_source = source_pool.get(video.content_id)
            if pooled_source is not None:
                return pooled_source
            source_number = len(pooled_sources)
            on_status(f"Building frame index: {video.display_name}")
            source: FileFrameSource = cls._run_in_background(
                lambda: cls._create_frame_source(video),
                cancel_loading=cancel_loading,
                dispose=lambda source: cls._dispose_sources([source], []),
            )
            rollback.callback(cls._dispose_sources, [source], [])
            pooled_source = _PooledVideoSource(
                video=video,
                source=source,
                relay=_FrameDeliveryRelay(source_number, parent_widget),
                source_index=source_number,
            )
            rollback.callback(cls._delete_later_if_supported, pooled_source.relay)
            source_pool[video.content_id] = pooled_source
            pooled_sources.append(pooled_source)
            return pooled_source

        for index, lane_content in enumerate(lane_contents):
            video = cls._require_seekable_video(lane_content.video)
            display = FrameDisplay()
            rollback.callback(display.cleanup)
            display.viewport.set_diagnostics_label(f"{video.display_name} · {lane_content.display_name}")
            controls = SeekableVideoControlPanel(display.viewport) if lane_count == 1 and use_lane_controls else None
            if controls is not None:
                display.mount_overlay(controls, position=OverlayPosition.BOTTOM_FULL_WIDTH)
            display.enable_side_panel()
            alignment_indicator = cls._add_lane_display(
                parent_widget, lane_layout, lane_count, index, lane_content, display
            )

            pooled_source = pooled_source_for(video)
            frame_timeline = pooled_source.source.get_frame_timeline()
            overlay_name = lane_content.overlay.display_name if lane_content.overlay is not None else None
            if overlay_name is not None:
                if lane_count == 1:
                    on_status(f"Parsing overlay data: {overlay_name}")
                else:
                    on_status(f"Parsing overlay data ({index + 1}/{lane_count}): {overlay_name}")
            overlay_source, overlay_policy = cls._run_in_background(
                lambda ov=lane_content.overlay: cls._create_overlay_source(ov, frame_timeline=frame_timeline),
                cancel_loading=cancel_loading,
                dispose=lambda result: cls._dispose_sources([], [result[0]] if result[0] is not None else []),
            )

            if overlay_source is not None:
                rollback.callback(cls._dispose_sources, [], [overlay_source])

            display.viewport.set_diagnostics_sources((overlay_source.diagnostics_id,) if overlay_source else ())
            timing_controls = TimingDiagnosticsWidget()
            lane_index = lane_indices[index]
            render_catalog_selection = render_catalog_manager.create_selection(
                visibility=lane_visibility.get(lane_index, OverlayVisibility()), parent=container
            )

            def remember_visibility(
                *, selection: SceneRenderCatalogSelection = render_catalog_selection, lane_index: int = lane_index
            ) -> None:
                lane_visibility[lane_index] = selection.visibility

            render_catalog_selection.selectionChanged.connect(remember_visibility)
            tools_panel = MediaToolsPanel(
                render_catalog_selection,
                filter_config=overlay_source.get_filter_config() if overlay_source is not None else None,
                overlay_settings=OverlayPersistenceSettings.default_enabled(),
                show_export=True,
                scene_history=cls._scene_history(overlay_source, overlay_policy, frame_timeline),
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
            lane.initialize_timing_diagnostics()
            lanes.append(lane)

        runtime = cls(
            lanes,
            container,
            source_pool=pooled_sources,
            cancel_loading=cancel_loading,
            parent=parent_widget,
        )
        rollback.callback(cls._delete_later_if_supported, runtime)
        for pooled_source in pooled_sources:
            pooled_source.relay.frameReady.connect(runtime._on_frame_ready)
            pooled_source.source.frameReady.connect(pooled_source.relay.deliver)
        runtime._connect_viewport_sync()
        runtime._connect_export_signals()
        runtime._connect_frame_requests()
        runtime._connect_timestamp_fallback_controls()
        return runtime

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

    @staticmethod
    def _create_frame_source(video: SeekableVideoContent) -> FileFrameSource:
        return FileFrameSource(
            str(video.source_spec.path),
            image_sequence_config=video.source_spec.image_sequence_config,
        )

    @staticmethod
    def _require_seekable_video(video: SeekableVideoContent | LiveVideoContent) -> SeekableVideoContent:
        if isinstance(video, SeekableVideoContent):
            return video
        raise TypeError("Offline viewer requires seekable video content.")

    @staticmethod
    def _scene_history(
        overlay_source: FileOverlaySource | None,
        overlay_policy: OverlayPersistencePolicy | None,
        frame_timeline: FrameTimeline,
    ) -> SceneHistory | None:
        """Place an overlay's history on the video as the lane's lookup and sticky selection show it."""
        if overlay_source is None or overlay_policy is None:
            return None
        allow_previous, max_sample_age_us = overlay_policy.sample_selection()
        return overlay_source.scene_history(
            frame_timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us
        )

    @staticmethod
    def _create_overlay_source(
        overlay: OverlayContent | None,
        *,
        frame_timeline: FrameTimeline | None = None,
    ) -> tuple[FileOverlaySource | None, OverlayPersistencePolicy | None]:
        if overlay is None:
            return None, None

        if not isinstance(overlay.source_spec, FileOverlaySourceSpec):
            raise RuntimeError(f"Offline overlay '{overlay.display_name}' requires a file overlay source spec.")

        try:
            source_spec = overlay.source_spec
            decoder_factory = get_file_decoder_factory(source_spec.handler_type)
            if source_spec.decoder_kwargs:
                decoder_factory = partial(decoder_factory, **source_spec.decoder_kwargs)
            overlay_source = FileOverlaySource(
                source_spec.path,
                decoder_factory,
                source_spec.handler_type,
                timestamp_fallback_policy=source_spec.timestamp_fallback_policy,
                frame_timeline=frame_timeline,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to create overlay source for {overlay.display_name}: {exc}") from exc

        overlay_policy = OverlayPersistencePolicy(OverlayPersistenceSettings.default_enabled())
        return overlay_source, overlay_policy

    @classmethod
    def _run_in_background(
        cls,
        fn: Callable[[], _T],
        *,
        cancel_loading: threading.Event,
        dispose: Callable[[_T], None] | None = None,
    ) -> _T:
        if cancel_loading.is_set():
            raise LoadingCancelled
        main_thread = QApplication.instance().thread()  # type: ignore[union-attr]
        result_box: list[_T] = []
        error_box: list[BaseException] = []

        def _worker() -> None:
            try:
                obj = fn()
                _move_qobjects_to_thread(obj, main_thread)
                result_box.append(obj)
            except BaseException as exc:
                error_box.append(exc)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        while thread.is_alive():
            # Retain ownership until construction settles, even after cancellation.
            QApplication.processEvents()
            thread.join(timeout=0.016)
        if cancel_loading.is_set():
            if result_box and dispose is not None:
                dispose(result_box[0])
            raise LoadingCancelled
        if error_box:
            raise error_box[0]
        return result_box[0]

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
        """Tear down the current runtime and stop all active sources."""
        self._is_playing = False
        sources_to_stop: list[FileFrameSource] = []
        overlays_to_close: list[FileOverlaySource] = []

        primary_source = self.get_primary_video_source()
        if primary_source is not None:
            self._disconnect_signal(getattr(primary_source, "sourceFinished", None), self._on_video_finished)

        self._disconnect_signal(self._secondary_frame_relay.frameReady, self._display_secondary_frame)

        for pooled_source in self._source_pool:
            self._disconnect_signal(getattr(pooled_source.source, "frameReady", None), pooled_source.relay.deliver)
            self._disconnect_signal(pooled_source.relay.frameReady, self._on_frame_ready)
            sources_to_stop.append(pooled_source.source)
            self._delete_later_if_supported(pooled_source.relay)
        self._source_pool.clear()

        for lane in self.lanes:
            if lane.overlay_source is not None:
                overlays_to_close.append(lane.overlay_source)
            lane.cleanup_display()
        self.lanes.clear()

        self.container.setParent(None)
        # Offline rebuilds replace the viewer synchronously; flush deferred deletion so
        # old renderer widgets are gone before the next runtime is installed.
        self._delete_later_if_supported(self.container, flush=True)

        if sources_to_stop or overlays_to_close:
            if blocking:
                self._shutdown_sources(sources_to_stop, overlays_to_close)
            else:
                self._run_in_background(
                    lambda: self._shutdown_sources(sources_to_stop, overlays_to_close),
                    cancel_loading=threading.Event(),
                )
            self._delete_sources(sources_to_stop, overlays_to_close)

        self.deleteLater()

    @classmethod
    def _dispose_sources(
        cls,
        video_sources: list[FileFrameSource],
        overlay_sources: list[FileOverlaySource],
    ) -> None:
        cls._shutdown_sources(video_sources, overlay_sources)
        cls._delete_sources(video_sources, overlay_sources)

    @staticmethod
    def _shutdown_sources(
        video_sources: list[FileFrameSource],
        overlay_sources: list[FileOverlaySource],
    ) -> None:
        for source in video_sources:
            try:
                source.stop()
            except Exception:
                pass
            try:
                source.wait(2000)
            except Exception:
                pass
        for overlay in overlay_sources:
            try:
                overlay.close()
            except Exception:
                pass

    @staticmethod
    def _disconnect_signal(signal: object | None, callback: object) -> None:
        if signal is None or not hasattr(signal, "disconnect"):
            return
        try:
            signal.disconnect(callback)
        except Exception:
            pass

    @staticmethod
    def _delete_later_if_supported(obj: object | None, *, flush: bool = False) -> None:
        if obj is None or not hasattr(obj, "deleteLater"):
            return
        try:
            obj.deleteLater()
        except Exception:
            pass
            return
        if flush and isinstance(obj, QObject):
            try:
                QCoreApplication.sendPostedEvents(obj, QEvent.Type.DeferredDelete)
            except Exception:
                pass

    @classmethod
    def _delete_sources(
        cls,
        video_sources: list[FileFrameSource],
        overlay_sources: list[FileOverlaySource],
    ) -> None:
        for source in video_sources:
            cls._delete_later_if_supported(source)
        for overlay in overlay_sources:
            cls._delete_later_if_supported(overlay)

    def _on_frame_ready(self, source_index: int, frame_data: FrameData) -> None:
        if source_index == 0 and self._is_stale_primary_frame(frame_data):
            return

        if source_index == 0:
            self._current_frame = self._bounded_frame(frame_data.frame_id.sequence_id)
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
        generation_getter = getattr(primary_source, "get_position_generation", None)
        if not callable(generation_getter):
            return
        generation = generation_getter()
        if isinstance(generation, int):
            self._min_valid_primary_generation = generation

    def _is_stale_primary_frame(self, frame_data: FrameData) -> bool:
        if self._min_valid_primary_generation is None or frame_data.metadata is None:
            return False
        generation = frame_data.metadata.get("position_generation")
        return isinstance(generation, int) and generation < self._min_valid_primary_generation

    def _bounded_frame(self, frame_number: int) -> int:
        if self.total_frames <= 0:
            return 0
        return max(0, min(frame_number, self.total_frames - 1))
