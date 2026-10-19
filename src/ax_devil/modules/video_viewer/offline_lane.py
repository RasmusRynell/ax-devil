"""One offline display lane: its display, controls, overlay lookup, export action and background analyses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtGui import QAction

from ax_devil.core.data_types import FrameData
from ax_devil.core.time_units import us_to_seconds
from ax_devil.modules.background_jobs import JobScope, LatestRequest
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.data_sources.timing_reports import OverlayAlignmentReport
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.synchronization import TimestampFallbackPolicy
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel, format_timecode
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.overlay_persistence import (
    OverlayPersistencePolicy,
)
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter
from ax_devil.modules.video_viewer.scene_inspection import deliver_scene_inspection_update
from ax_devil.modules.video_viewer.timing_diagnostics_widget import (
    OverlayAlignmentIndicator,
    TimingDiagnosticsWidget,
)
from ax_devil.modules.workspace.core import SeekableVideoContent


def _stale_message(what: str, lane_name: str, error: Exception, retry_settings: str) -> str:
    """Say that the *what* shown for a lane is out of date because refreshing it failed, and what retries it."""
    reason = str(error) or type(error).__name__
    return f"{what} of lane '{lane_name}' is out of date: {reason}. Change {retry_settings} to retry."


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


@dataclass(slots=True)
class OfflineLane:
    """Per-lane runtime state for one offline display lane.

    Uses a `FrameDisplay` instance for rendering and related lane-owned helpers. *export_action* exports this lane; the
    media tools button and the lane's context menu both trigger it, and ``set_export_allowed`` and the loaded render
    catalog decide whether it is enabled. *lane_index* is the lane's position in its entry.
    """

    content: SeekableVideoContent
    name: str
    display: FrameDisplay
    overlay_source: FileOverlaySource | None
    export_action: QAction
    lane_index: int = 0
    controls: SeekableVideoControlPanel | None = None
    overlay_policy: OverlayPersistencePolicy | None = None
    presenter: SceneFramePresenter | None = None
    tools_panel: MediaToolsPanel | None = None
    filter_model: SessionFilter | None = None
    timing_controls: TimingDiagnosticsWidget | None = None
    alignment_indicator: OverlayAlignmentIndicator | None = None
    video_source: FileFrameSource | None = None
    source_index: int = 0
    analysis_jobs: JobScope | None = None  # The session's; the lane refreshes nothing without it.
    _history_request: LatestRequest = field(init=False, repr=False)
    _alignment_request: LatestRequest = field(init=False, repr=False)
    _export_allowed: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        self._history_request = LatestRequest(self.display)
        self._alignment_request = LatestRequest(self.display)

    def set_export_allowed(self, allowed: bool) -> None:
        """Allow or forbid export, such as while an export dialog is open; it also needs a loaded render catalog."""
        self._export_allowed = allowed
        self.update_export_action()

    def update_export_action(self) -> None:
        """Enable export once the lane has a render catalog and nothing forbids it, and say why when it cannot."""
        ready = self.presenter is not None and self.presenter.scene_render_catalog is not None
        self.export_action.setEnabled(self._export_allowed and ready)
        self.export_action.setToolTip(
            "Export video with overlays" if ready else "Export is available once the render catalog has loaded"
        )

    def display_frame(self, frame: VideoFrameWithOverlays) -> None:
        """Display a synced frame and update lane-owned controls."""
        self.display.display_frame(frame)
        if self.controls is not None and frame.frame.frame_id is not None:
            self.controls.set_current_frame(frame.frame.frame_id, us_to_seconds(frame.frame.timestamp_us))

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
        deliver_scene_inspection_update(self.tools_panel, presentation.inspection)

    def show_video_on_controls(self, source: FileFrameSource) -> None:
        """Let the lane controls follow *source*'s frames, time and cache."""
        if self.controls is not None:
            show_video_on_controls(self.controls, source)

    def sync_playback_state(self, playing: bool) -> None:
        """Synchronize caller-owned controls with the runtime playback state."""
        if self.tools_panel is not None:
            self.tools_panel.set_playing(playing)
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
        """Apply a timestamp fallback policy now and refresh alignment status and history in the background."""
        if self.overlay_source is None:
            return
        self.overlay_source.set_timestamp_fallback_policy(policy)
        self.refresh_alignment_report()
        self.refresh_scene_history()

    def refresh_scene_history(self) -> None:
        """Place the overlay history again after a change in how lookup selects samples.

        The placement runs on a worker over values copied now; the panel keeps the previous history until the newest
        request finishes. A failed one leaves the previous history in place, marked out of date.
        """
        if (
            self.tools_panel is None
            or self.video_source is None
            or self.overlay_source is None
            or self.overlay_policy is None
            or self.analysis_jobs is None
        ):
            return
        allow_previous, max_sample_age_us = self.overlay_policy.sample_selection()
        samples = self.overlay_source.samples()
        timeline = self.video_source.get_frame_timeline().snapshot()
        self._history_request.submit(
            self.analysis_jobs,
            f"scene history of lane '{self.name}'",
            lambda job: samples.scene_history(
                timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us, token=job.token
            ),
            on_result=self._show_history,
            on_error=self._history_failed,
        )

    def _show_history(self, history: SceneHistory) -> None:
        if self.tools_panel is not None:
            self.tools_panel.set_scene_history(history)

    def _history_failed(self, error: Exception) -> None:
        if self.tools_panel is not None:
            self.tools_panel.mark_history_out_of_date(
                _stale_message("Scene history", self.name, error, "an overlay sync or sticky setting")
            )

    def refresh_alignment_report(self) -> None:
        """Refresh the visible overlay alignment report in the background.

        The previous report stays until the newest request finishes. A failed one leaves it in place, marked out of
        date.
        """
        if self.overlay_source is None or self.video_source is None or self.analysis_jobs is None:
            return
        samples = self.overlay_source.samples()
        handler_type = self.overlay_source.handler_type
        timeline = self.video_source.get_frame_timeline().snapshot()
        self._alignment_request.submit(
            self.analysis_jobs,
            f"overlay alignment of lane '{self.name}'",
            lambda job: samples.analyze_alignment(handler_type, timeline, job.token),
            on_result=self._show_alignment_report,
            on_error=self._alignment_failed,
        )

    def _show_alignment_report(self, report: OverlayAlignmentReport | None) -> None:
        if self.timing_controls is None or self.alignment_indicator is None:
            return
        self.timing_controls.update_alignment_report(report)
        self.alignment_indicator.update_alignment_report(report)

    def _alignment_failed(self, error: Exception) -> None:
        if self.timing_controls is None or self.alignment_indicator is None:
            return
        message = _stale_message("Overlay alignment", self.name, error, "an overlay sync setting")
        self.timing_controls.mark_alignment_out_of_date(message)
        self.alignment_indicator.mark_alignment_out_of_date(message)

    def cleanup_display(self) -> None:
        """Release display-owned mount widgets before the lane container is deleted."""
        self._history_request.close()
        self._alignment_request.close()
        if self.presenter is not None:
            self.presenter.clear()
        if self.tools_panel is not None:
            self.tools_panel.cleanup()
        self.display.cleanup()
