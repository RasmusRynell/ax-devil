"""One offline display lane: presentation, overlay selection, export inputs and background analyses."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtGui import QAction

from ax_devil.core.data_types import FrameData
from ax_devil.modules.background_jobs import JobScope, LatestRequest
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.data_sources.timing_reports import OverlayAlignmentReport
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.rendering import SceneRenderCatalog
from ax_devil.modules.synchronization import TimestampFallbackPolicy
from ax_devil.modules.video_player.ui.controls import format_timecode
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.export import ExportLane
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_entry_media import LaneOverlay
from ax_devil.modules.video_viewer.overlay_persistence import (
    OverlayPersistencePolicy,
    OverlayPersistenceSettings,
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


@dataclass
class OfflineLane:
    """Per-lane runtime state for one offline display lane.

    Uses a `FrameDisplay` instance for rendering and related lane-owned helpers. *export_action* exports this lane; the
    media tools button and the lane's context menu both trigger it, and ``set_export_allowed`` and the loaded render
    catalog decide whether it is enabled. *lane_index* is the lane's position in its entry.
    """

    content: SeekableVideoContent
    name: str
    display: FrameDisplay
    overlay: LaneOverlay | None
    export_action: QAction
    presenter: SceneFramePresenter
    tools_panel: MediaToolsPanel
    filter_model: SessionFilter
    timing_controls: TimingDiagnosticsWidget
    alignment_indicator: OverlayAlignmentIndicator
    video_source: FileFrameSource
    analysis_jobs: JobScope
    lane_index: int = 0
    source_index: int = 0
    _last_frame: FrameData | None = field(default=None, init=False, repr=False)
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
        ready = self.presenter.scene_render_catalog is not None
        self.export_action.setEnabled(self._export_allowed and ready)
        self.export_action.setToolTip(
            "Export video with overlays" if ready else "Export is available once the render catalog has loaded"
        )

    def present_frame(self, frame_data: FrameData) -> None:
        """Present a frame and remember it for settings changes while playback is paused."""
        self._last_frame = frame_data
        selection = (
            self.overlay.policy.select_from_source(self.overlay.source, frame_data.frame_id) if self.overlay else None
        )
        presentation = self.presenter.prepare_frame(
            frame_data,
            selection.overlay if selection else None,
            overlay_metadata=selection.to_metadata() if selection else None,
        )
        self.display.display_frame(presentation.display_frame)
        deliver_scene_inspection_update(self.tools_panel, presentation.inspection)

    def apply_overlay_persistence(self, settings: OverlayPersistenceSettings) -> None:
        """Apply sticky settings to the held frame and refresh history under the same selection rules."""
        if self.overlay is None:
            return
        self.overlay.policy.update_settings(settings)
        self._represent()
        self.refresh_scene_history()

    def apply_catalog(self, catalog: SceneRenderCatalog) -> None:
        """Attach a catalog before refreshing the display and export availability."""
        self.presenter.attach_scene_render_catalog(catalog)
        self.display.refresh_overlays()
        self.update_export_action()

    def _represent(self) -> None:
        if self._last_frame is not None:
            self.present_frame(self._last_frame)

    def export_lane(self) -> ExportLane | None:
        """Freeze the lane's current presentation inputs, or wait for its first catalog."""
        catalog = self.presenter.scene_render_catalog
        if catalog is None:
            return None
        return ExportLane(
            name=self.name or self.content.display_name,
            video_source=self.video_source,
            presenter=SceneFramePresenter(scene_filter=self.filter_model.snapshot(), scene_render_catalog=catalog),
            overlay_source=self.overlay.source if self.overlay else None,
            overlay_policy=OverlayPersistencePolicy(self.overlay.policy.settings.copy()) if self.overlay else None,
        )

    def initialize_timing_diagnostics(self, alignment_report: OverlayAlignmentReport | None) -> None:
        """Populate timing controls and alignment status from this lane's sources and opening-time report."""
        self.timing_controls.update_timing_profile(self.video_source.get_timing_profile())
        self._show_alignment_report(alignment_report)
        if self.overlay is None:
            self.timing_controls.set_timestamp_fallback_controls_enabled(False)
            return
        self.timing_controls.set_timestamp_fallback_controls_enabled(True)
        self.timing_controls.set_timestamp_fallback_policy(self.overlay.source.get_timestamp_fallback_policy())

    def apply_timestamp_policy(self, policy: TimestampFallbackPolicy) -> None:
        """Apply a timestamp fallback policy now and refresh alignment status and history in the background."""
        if self.overlay is None:
            return
        self.overlay.source.set_timestamp_fallback_policy(policy)
        self._represent()
        self.refresh_alignment_report()
        self.refresh_scene_history()

    def refresh_scene_history(self) -> None:
        """Place the overlay history again after a change in how lookup selects samples.

        The placement runs on a worker over values copied now; the panel keeps the previous history until the newest
        request finishes. A failed one leaves the previous history in place, marked out of date.
        """
        if self.overlay is None:
            return
        allow_previous, max_sample_age_us = self.overlay.policy.sample_selection()
        samples = self.overlay.source.samples()
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
        self.tools_panel.set_scene_history(history)

    def _history_failed(self, error: Exception) -> None:
        self.tools_panel.mark_history_out_of_date(
            _stale_message("Scene history", self.name, error, "an overlay sync or sticky setting")
        )

    def refresh_alignment_report(self) -> None:
        """Refresh the visible overlay alignment report in the background.

        The previous report stays until the newest request finishes. A failed one leaves it in place, marked out of
        date.
        """
        if self.overlay is None:
            return
        samples = self.overlay.source.samples()
        handler_type = self.overlay.source.handler_type
        timeline = self.video_source.get_frame_timeline().snapshot()
        self._alignment_request.submit(
            self.analysis_jobs,
            f"overlay alignment of lane '{self.name}'",
            lambda job: samples.analyze_alignment(handler_type, timeline, job.token),
            on_result=self._show_alignment_report,
            on_error=self._alignment_failed,
        )

    def _show_alignment_report(self, report: OverlayAlignmentReport | None) -> None:
        self.timing_controls.update_alignment_report(report)
        self.alignment_indicator.update_alignment_report(report)

    def _alignment_failed(self, error: Exception) -> None:
        message = _stale_message("Overlay alignment", self.name, error, "an overlay sync setting")
        self.timing_controls.mark_alignment_out_of_date(message)
        self.alignment_indicator.mark_alignment_out_of_date(message)

    def cleanup_display(self) -> None:
        """Release display-owned mount widgets before the lane container is deleted."""
        self._history_request.close()
        self._alignment_request.close()
        self._last_frame = None
        self.presenter.clear()
        self.tools_panel.cleanup()
        self.display.cleanup()
