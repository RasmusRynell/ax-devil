"""Offline video viewer for local video playback."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ax_devil.core.playback_speed import (
    DEFAULT_PLAYBACK_SPEED,
    clamp_playback_speed,
    step_playback_speed,
)
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.scene.rendering import OverlayVisibility, SceneRenderCatalogManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.constants import MOUSE_IDLE_HIDE_DELAY
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.video_player.orchestration.control_visibility import ControlVisibilityController
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_viewer.loading_indicator import LoadingIndicator
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia, EntryOpening, release_media
from ax_devil.modules.video_viewer.offline_viewer_navigation import OfflineViewerNavigation
from ax_devil.modules.video_viewer.offline_viewer_runtime import (
    OfflineSession,
    show_video_on_controls,
    video_details,
)
from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    ConsiderationQuery,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.viewer_host import WorkspaceWidget

logger = get_logger(__name__)


class _ViewerOverlayHost(QWidget):
    """Fill available space with viewer content and anchor one overlay at the bottom."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content_layout = QGridLayout(self)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

    def set_content(self, content: QWidget) -> None:
        self.content_layout.addWidget(content, 0, 0)

    def set_bottom_overlay(self, overlay: QWidget) -> None:
        self.content_layout.addWidget(overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)
        overlay.raise_()


class OfflineVideoViewerWidget(WorkspaceWidget):
    """Offline video viewer for local playback with multi-lane support."""

    current_entry_changed = Signal(int)

    def __init__(
        self,
        content: SeekableVideoContent | PlaylistContent,
        render_catalog_manager: SceneRenderCatalogManager,
        start_index: int = 0,
        parent: QWidget | None = None,
        consideration_query: ConsiderationQuery | None = None,
    ) -> None:
        self._content = content
        self._render_catalog_manager = render_catalog_manager
        self._navigation = OfflineViewerNavigation(content, consideration_query)
        self._current_index = self._navigation.normalize_entry_index(start_index)
        self._runtime: OfflineSession | None = None
        self._viewer_host: _ViewerOverlayHost | None = None
        self._viewer_host_layout: QGridLayout | None = None
        self._nav_label: QLabel | None = None
        self._prev_button: QPushButton | None = None
        self._next_button: QPushButton | None = None
        self._navigation_controls: QWidget | None = None
        self._global_controls: SeekableVideoControlPanel | None = None
        self._global_control_visibility: ControlVisibilityController | None = None
        self._use_global_controls = False
        self._is_cleaned_up = False
        self._opening: EntryOpening | None = None
        self._loading_indicator: LoadingIndicator | None = None
        self._playback_speed = DEFAULT_PLAYBACK_SPEED
        self._initial_entry_loaded = False
        self._lane_visibility: dict[int, OverlayVisibility] = {}
        super().__init__(parent)

    def _get_entries(self) -> tuple[PlaylistEntry, ...]:
        """Return the playlist entries for the current content."""
        return self._navigation.get_entries()

    def _is_entry_considered(self, entry_index: int) -> bool:
        """Return whether a playlist entry should participate in stepping."""
        return self._navigation.is_entry_considered(entry_index)

    def _is_lane_considered(self, entry_index: int, lane_index: int) -> bool:
        """Return whether a lane should participate in active entry layout."""
        return self._navigation.is_lane_considered(entry_index, lane_index)

    def _setup_widget_ui(self) -> None:
        layout = self.get_content_layout()
        entries = self._get_entries()
        if not entries:
            layout.addWidget(QLabel("No local content selected"))
            return

        if len(entries) > 1:
            self._navigation_controls = self._build_navigation_controls()

        self._viewer_host = _ViewerOverlayHost(self)
        self._viewer_host_layout = self._viewer_host.content_layout
        layout.addWidget(self._viewer_host, 1)

    def on_workspace_attached(self) -> None:
        """Start the initial entry load once the viewer is visible in the workspace."""
        if self._initial_entry_loaded or not self._get_entries():
            return
        self._open_entry(self._current_index)

    def _build_navigation_controls(self) -> QWidget:
        navigation = QWidget(self)
        navigation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        nav_layout = QHBoxLayout(navigation)
        nav_layout.setContentsMargins(Space.M, 0, Space.M, 0)
        nav_layout.setSpacing(Space.S)
        nav_layout.addStretch(1)

        self._prev_button = QPushButton(Icon.STEP_BACK.icon(), "")
        self._prev_button.setToolTip("Previous entry")
        self._prev_button.setAccessibleName("Previous entry")
        self._prev_button.clicked.connect(self._step_prev)
        nav_layout.addWidget(self._prev_button)

        self._nav_label = QLabel()
        nav_layout.addWidget(self._nav_label)

        self._next_button = QPushButton(Icon.STEP_FORWARD.icon(), "")
        self._next_button.setToolTip("Next entry")
        self._next_button.setAccessibleName("Next entry")
        self._next_button.clicked.connect(self._step_next)
        nav_layout.addWidget(self._next_button)
        nav_layout.addStretch(1)

        return navigation

    def _open_entry(self, index: int) -> None:
        """Show entry *index*: drop any entry still opening, release the shown one, and open *index* in the background.

        The only way an entry is shown. The viewer stays responsive while sources open, and a newer request simply
        replaces an older one.
        """
        if self._is_cleaned_up:
            return
        self._initial_entry_loaded = True
        if self._opening is not None:
            self._opening.abandon()
            self._opening = None
        self._teardown_runtime()
        self._current_index = index
        self._update_nav_state()
        self.current_entry_changed.emit(index)
        self.on_screen_item_changed.emit(self.current_on_screen_item())

        entry = self._get_entries()[index]
        lane_indices = tuple(
            lane_index for lane_index in range(len(entry.lanes)) if self._is_lane_considered(index, lane_index)
        )
        indicator = self._show_loading_indicator()
        opening = EntryOpening(on_status=indicator.set_message, on_opened=partial(self._on_entry_opened, index))
        self._opening = opening
        opening.start(entry, lane_indices)

    def _on_entry_opened(self, index: int, media: EntryMedia) -> None:
        self._opening = None
        self._hide_loading_indicator()
        error = media.error
        runtime: OfflineSession | None = None
        if error is None:
            try:
                runtime = OfflineSession.build(
                    self,
                    media,
                    render_catalog_manager=self._render_catalog_manager,
                    use_lane_controls=self._entry_count() == 1,
                    lane_visibility=self._lane_visibility,
                    pane_title=self.get_display_name(),
                )
            except Exception as exc:
                logger.exception(f"Failed to build displays for entry {index + 1}")
                error = str(exc)
        if runtime is None:
            release_media(media)
            QMessageBox.warning(self, "Offline Viewer", f"Failed to load entry {index + 1}: {error}")
            return
        self._install_entry(runtime)

    def _show_loading_indicator(self) -> LoadingIndicator:
        if self._loading_indicator is None:
            overlay_parent = self._viewer_host or self
            indicator = LoadingIndicator(overlay_parent)
            indicator.setGeometry(overlay_parent.rect())
            indicator.raise_()
            indicator.show()
            indicator.start()
            self._loading_indicator = indicator
        self._loading_indicator.set_message("")
        return self._loading_indicator

    def _hide_loading_indicator(self) -> None:
        indicator = self._loading_indicator
        if indicator is None:
            return
        self._loading_indicator = None
        indicator.stop()
        indicator.deleteLater()

    def _install_entry(self, runtime: OfflineSession) -> None:
        self._runtime = runtime
        runtime.currentFrameChanged.connect(self._sync_global_current_frame)
        runtime.playbackStateChanged.connect(self._sync_global_playback_state)
        runtime.playbackFinished.connect(lambda: self._sync_global_playback_state(False))

        runtime.set_playback_speed(self._playback_speed)

        if self._viewer_host is not None:
            self._viewer_host.set_content(runtime.container)
        self._set_global_controls_enabled(runtime.has_multiple_lanes or self._entry_count() > 1)
        self._connect_global_control_visibility(runtime)

        primary_controls = runtime.primary_controls
        if primary_controls is not None and not self._has_active_global_controls():
            primary_controls.playRequested.connect(self._start_playback)
            primary_controls.pauseRequested.connect(self._pause_playback)
            primary_controls.jumpToRequested.connect(self._jump_to_frame)
            primary_controls.frameStepRequested.connect(self._step_frames)
            primary_controls.playbackSpeedChanged.connect(self.set_playback_speed)
            primary_controls.set_playback_speed(self._playback_speed)

        primary_source = runtime.get_primary_video_source()
        if self._has_active_global_controls() and self._global_controls is not None and primary_source is not None:
            show_video_on_controls(self._global_controls, primary_source)
        # Lanes of different videos have no single size or length to show.
        sources = runtime.iter_video_sources()
        self.set_header_details(video_details(sources[0]) if len(sources) == 1 else "")

        self._jump_to_frame(0)
        self._start_playback()

    def _has_active_global_controls(self) -> bool:
        return self._use_global_controls and self._global_controls is not None

    def _set_global_controls_enabled(self, enabled: bool) -> None:
        self._use_global_controls = enabled
        if enabled:
            if self._global_controls is None:
                controls = SeekableVideoControlPanel(self)
                controls.playRequested.connect(self._start_playback)
                controls.pauseRequested.connect(self._pause_playback)
                controls.jumpToRequested.connect(self._jump_to_frame)
                controls.frameStepRequested.connect(self._step_frames)
                controls.playbackSpeedChanged.connect(self.set_playback_speed)
                controls.set_context_widget(self._navigation_controls)
                self._global_controls = controls
                self._global_control_visibility = ControlVisibilityController(
                    timer_parent=self,
                    idle_hide_delay_ms=MOUSE_IDLE_HIDE_DELAY,
                    request_show=controls.fade_in,
                    request_hide=controls.start_auto_hide_timer,
                )
                if self._viewer_host is not None:
                    self._viewer_host.set_bottom_overlay(controls)
            self._global_controls.show()
            self._global_controls.raise_()
            self._global_controls.fade_in()
            self._global_controls.sync_playback_state(False)
            self._global_controls.set_playback_speed(self._playback_speed, emit_signals=False)
            return

        if self._global_controls is not None:
            self._global_controls.sync_playback_state(False)
            self._global_controls.hide()

    def _connect_global_control_visibility(self, runtime: OfflineSession) -> None:
        """Let pointer activity in any lane control the shared transport overlay."""
        if (
            not self._has_active_global_controls()
            or self._global_controls is None
            or self._global_control_visibility is None
        ):
            return
        self._global_controls.set_hover_sink(self._global_control_visibility)
        for lane in runtime.lanes:
            lane.display.controlsRequested.connect(self._global_control_visibility.on_mouse_move)
            lane.display.controlsHideRequested.connect(self._global_control_visibility.on_mouse_leave)

    def _sync_global_current_frame(self, frame: int) -> None:
        if self._has_active_global_controls() and self._global_controls is not None:
            seconds = self._runtime.current_frame_seconds if self._runtime is not None else None
            self._global_controls.set_current_frame(frame, seconds)

    def _sync_global_playback_state(self, playing: bool) -> None:
        if self._has_active_global_controls() and self._global_controls is not None:
            self._global_controls.sync_playback_state(playing)

    def _start_playback(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.start_playback()

    def _pause_playback(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.pause_playback()

    def _jump_to_frame(self, frame_num: int) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.jump_to_frame(frame_num)

    def _step_frames(self, delta: int) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.step_frames(delta)

    # ------------------------------------------------------------------
    # Public API for shortcut routing
    # ------------------------------------------------------------------

    def pause_playback(self) -> None:
        """Pause playback if currently playing. No-op if already paused."""
        runtime = self._runtime
        if runtime is not None and runtime.is_playing:
            runtime.pause_playback()

    def export_video(self) -> None:
        """Export the shown entry, letting the user choose which lanes to include."""
        if self._runtime is not None:
            self._runtime.export_lanes()

    def toggle_playback(self) -> None:
        """Toggle between playing and paused states."""
        runtime = self._runtime
        if runtime is None:
            return
        if runtime.is_playing:
            runtime.pause_playback()
        else:
            runtime.start_playback()

    def step_frames(self, delta: int) -> None:
        """Step playback by *delta* frames (positive=forward, negative=backward)."""
        self._step_frames(delta)

    def step_next_entry(self) -> None:
        """Navigate to the next playlist entry."""
        self._step_next()

    def step_prev_entry(self) -> None:
        """Navigate to the previous playlist entry."""
        self._step_prev()

    def toggle_media_tools(self) -> None:
        """Show or hide the media tools panel of the focused lane."""
        display = self._runtime.focused_display() if self._runtime is not None else None
        if display is not None:
            display.set_side_panel_open(not display.is_side_panel_open())

    def zoom(self, step: ZoomStep) -> None:
        """Apply one keyboard zoom step; the other lanes follow through viewport sync."""
        runtime = self._runtime
        if runtime is not None:
            runtime.zoom(step)

    def toggle_info_overlay(self) -> None:
        """Toggle the info overlay on all active offline displays."""
        runtime = self._runtime
        if runtime is None:
            return
        for display in runtime.displays:
            display.viewport.toggle_info_overlay()

    def set_playback_speed(self, speed: float) -> None:
        """Set playback speed for the active offline runtime."""
        clamped = clamp_playback_speed(speed)
        self._playback_speed = clamped

        runtime = self._runtime
        if runtime is not None:
            runtime.set_playback_speed(clamped)

        if self._global_controls is not None:
            self._global_controls.set_playback_speed(clamped, emit_signals=False)

        if runtime is not None and not self._has_active_global_controls() and runtime.primary_controls is not None:
            runtime.primary_controls.set_playback_speed(clamped, emit_signals=False)

    def increase_playback_speed(self) -> None:
        """Increase playback speed by one step for shortcut routing."""
        self.set_playback_speed(step_playback_speed(self._playback_speed, 1, floor=0.1))

    def decrease_playback_speed(self) -> None:
        """Decrease playback speed by one step for shortcut routing."""
        self.set_playback_speed(step_playback_speed(self._playback_speed, -1, floor=0.1))

    def _entry_count(self) -> int:
        return len(self._get_entries())

    def _find_prev_considered_index(self) -> int | None:
        return self._navigation.find_prev_considered_index(self._current_index)

    def _find_next_considered_index(self) -> int | None:
        return self._navigation.find_next_considered_index(self._current_index)

    def _replacement_index_for_current_entry(self) -> int | None:
        return self._navigation.replacement_index_for_current_entry(self._current_index)

    def _update_nav_state(self) -> None:
        if self._nav_label is not None:
            self._nav_label.setText(f"Entry {self._current_index + 1} / {self._entry_count()}")
        if self._prev_button is not None:
            self._prev_button.setEnabled(self._find_prev_considered_index() is not None)
        if self._next_button is not None:
            self._next_button.setEnabled(self._find_next_considered_index() is not None)

    def _step_prev(self) -> None:
        previous_index = self._find_prev_considered_index()
        if previous_index is not None:
            self._open_entry(previous_index)

    def _step_next(self) -> None:
        next_index = self._find_next_considered_index()
        if next_index is not None:
            self._open_entry(next_index)

    def _teardown_runtime(self, *, blocking: bool = False) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        self._runtime = None
        self.set_header_details("")
        runtime.cleanup(blocking=blocking)

    def get_display_name(self) -> str:
        return self._content.display_name

    def current_on_screen_item(self) -> OnScreenWorkspaceItem:
        """Return the workspace item shown by this viewer."""
        return self._content.on_screen_item(self._current_index)

    def get_contents(self) -> list[SeekableVideoContent]:
        entries = self._get_entries()
        if not entries or self._current_index >= len(entries):
            return []
        return list(dict.fromkeys(lane.video for lane in entries[self._current_index].lanes))

    def cleanup(self) -> None:
        if self._is_cleaned_up:
            return
        self._is_cleaned_up = True
        if self._opening is not None:
            self._opening.abandon()
            self._opening = None
        self._hide_loading_indicator()
        self._teardown_runtime(blocking=True)
        self._nav_label = None
        self._prev_button = None
        self._next_button = None
        self._navigation_controls = None
        if self._global_control_visibility is not None:
            self._global_control_visibility.cleanup()
        self._global_control_visibility = None
        if self._global_controls is not None:
            self._global_controls.cleanup()
        self._global_controls = None

    def refresh_item_consideration(self, item_ref: ConsiderationItemRef, _considered: bool) -> None:
        """Refresh viewer state after an item consideration change."""
        if self._is_cleaned_up:
            return
        if self._content.content_id != item_ref.content_id:
            return

        if item_ref.kind == "video_lane":
            self._open_entry(self._current_index)
            return

        if item_ref.kind == "playlist_entry" and item_ref.entry_index == self._current_index:
            if self._is_entry_considered(self._current_index):
                self._update_nav_state()
                return
            replacement_index = self._replacement_index_for_current_entry()
            if replacement_index is not None:
                self._open_entry(replacement_index)
            else:
                self._update_nav_state()
            return

        self._update_nav_state()
        if item_ref.kind == "playlist_lane" and item_ref.entry_index == self._current_index:
            self._open_entry(self._current_index)
