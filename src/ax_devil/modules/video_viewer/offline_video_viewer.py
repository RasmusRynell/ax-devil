"""Offline video viewer for local video playback."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QPoint, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ax_devil.core.playback_speed import step_playback_speed
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.background_jobs import LatestRequest
from ax_devil.modules.scene.rendering import OverlayVisibility, SceneRenderCatalogManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.video_viewer.entry_preparation import entry_preparation, prepare_entry
from ax_devil.modules.video_viewer.loading_indicator import LoadingIndicator
from ax_devil.modules.video_viewer.offline_entry_media import (
    EntryMedia,
    entry_opening_scope,
    open_entry_media,
    release_media,
)
from ax_devil.modules.video_viewer.offline_lane import OfflineLane, video_details
from ax_devil.modules.video_viewer.offline_transport import OfflineTransport, TransportCommands
from ax_devil.modules.video_viewer.offline_viewer_navigation import OfflineViewerNavigation
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineSession
from ax_devil.modules.video_viewer.offline_viewer_widgets import EntryNavigationBar, ViewerOverlayHost, exec_lane_menu
from ax_devil.modules.video_viewer.view_settings import view_defaults
from ax_devil.modules.workspace.core import (
    BookmarkPosition,
    BookmarkTarget,
    ConsiderationItemRef,
    ConsiderationQuery,
    PlaylistContent,
    SeekableVideoContent,
    entry_target,
)
from ax_devil.modules.workspace.core.consideration import OnScreenWorkspaceItem
from ax_devil.modules.workspace.ui.viewer_widget import SHARED_ACTION_IDS, ViewerWidget

logger = get_logger(__name__)


class OfflineVideoViewerWidget(ViewerWidget):
    """Offline video viewer for local playback with multi-lane support.

    An explicit *start_index* opens that entry even when hidden; otherwise the first considered entry opens.
    It plays the entry, or shows *start_frame* paused when given; later entries start at their first frame.
    """

    current_entry_changed = Signal(int)

    def __init__(
        self,
        content: SeekableVideoContent | PlaylistContent,
        render_catalog_manager: SceneRenderCatalogManager,
        start_index: int | None = None,
        parent: QWidget | None = None,
        consideration_query: ConsiderationQuery | None = None,
        start_frame: int | None = None,
    ) -> None:
        self._content = content
        self._display_name = content.display_name
        self._render_catalog_manager = render_catalog_manager
        self._navigation = OfflineViewerNavigation(content, consideration_query)
        self._current_index = (
            self._navigation.normalize_entry_index(0)
            if start_index is None
            else self._navigation.bound_entry_index(start_index)
        )
        self._start_frame = start_frame
        self._pending_lane_focus: tuple[int, int] | None = None
        self._runtime: OfflineSession | None = None
        self._viewer_host: ViewerOverlayHost | None = None
        self._entry_bar: EntryNavigationBar | None = None
        self._is_cleaned_up = False
        self._loading_indicator: LoadingIndicator | None = None
        self._initial_entry_loaded = False
        self._lane_visibility: dict[int, OverlayVisibility] = {}
        self._cancel_preparation: Callable[[], None] | None = None
        super().__init__(parent)
        self._transport = OfflineTransport(
            self,
            TransportCommands(
                self._start_playback,
                self._pause_playback,
                self._jump_to_frame,
                self._step_frames,
                self.set_playback_speed,
            ),
            context=self._entry_bar,
            host=self._viewer_host,
        )
        self._opening = LatestRequest(self)
        self._opening_jobs = entry_opening_scope(self)
        GlobalSettings().prepare_next_video_changed.connect(self._prepare_next_if_open)

    def _setup_widget_ui(self) -> None:
        layout = self.get_content_layout()
        entries = self._navigation.get_entries()
        if not entries:
            layout.addWidget(QLabel("No local content selected"))
            return

        if len(entries) > 1:
            self._entry_bar = EntryNavigationBar(on_previous=self._step_prev, on_next=self._step_next)

        self._viewer_host = ViewerOverlayHost(self)
        layout.addWidget(self._viewer_host, 1)

    def on_workspace_attached(self) -> None:
        """Start the initial entry load once the viewer is visible in the workspace."""
        if self._initial_entry_loaded or not self._navigation.get_entries():
            return
        self._open_entry(self._current_index, self._start_frame)

    def _open_entry(self, index: int, start_frame: int | None = None, *, keep_shown: bool = False) -> None:
        """Show entry *index*: drop any entry still opening and open *index* in the background.

        The only way an entry is shown. The viewer stays responsive while sources open, and a newer request simply
        replaces an older one. The entry plays from its first frame, or shows *start_frame* paused. A loading indicator
        covers the viewer meanwhile. Unless *keep_shown*, the shown entry is released first; with it, the shown entry
        stays beneath the indicator until the new media is ready, and stays if that cannot be shown.
        """
        if self._is_cleaned_up:
            return
        self._initial_entry_loaded = True
        if self._pending_lane_focus is not None and self._pending_lane_focus[0] != index:
            self._pending_lane_focus = None
        self._opening.cancel()
        self._stop_preparation()
        if not keep_shown:
            if self._holds_focus():
                # Tearing down a focused display hands the focus to another widget, such as a search field.
                self.setFocus(Qt.FocusReason.OtherFocusReason)
            self._teardown_runtime()
            self._current_index = index
            self._start_frame = start_frame
            self._update_nav_state()
            self.current_entry_changed.emit(index)
            self.on_screen_item_changed.emit(self.current_on_screen_item())

        entry = self._navigation.get_entries()[index]
        lane_indices = self._considered_lanes(index)
        indicator = self._show_loading_indicator()
        self._opening.submit(
            self._opening_jobs,
            "open offline entry",
            partial(
                open_entry_media,
                entry,
                lane_indices,
                QThread.currentThread(),
                defaults=view_defaults(),
            ),
            on_result=partial(self._on_entry_opened, index, start_frame, keep_shown),
            on_error=lambda error: self._on_entry_opened(index, start_frame, keep_shown, EntryMedia(error=str(error))),
            on_progress=indicator.set_message,
            on_discard=release_media,
        )

    def _considered_lanes(self, index: int) -> tuple[int, ...]:
        """Return the lanes of entry *index* that take part in its layout."""
        return tuple(
            lane_index
            for lane_index in range(len(self._navigation.get_entries()[index].lanes))
            if self._navigation.is_lane_considered(index, lane_index)
        )

    def _prepare_next(self) -> None:
        """Let a background process warm the caches of the entry that playback steps to next, if the user allows it."""
        if not GlobalSettings().prepare_next_video:
            self._stop_preparation()
            return
        entries = self._navigation.get_entries()
        next_index = self._navigation.find_next_considered_index(self._current_index)
        request = (
            entry_preparation(
                entries[next_index],
                self._considered_lanes(next_index),
            )
            if next_index is not None
            else None
        )
        if request is None:
            self._stop_preparation()
        else:
            self._cancel_preparation = prepare_entry(request)

    def _prepare_next_if_open(self) -> None:
        """Start or stop preparing when the setting changes while an entry is open."""
        if self._runtime is not None:
            self._prepare_next()

    def _stop_preparation(self) -> None:
        if self._cancel_preparation is not None:
            self._cancel_preparation()
            self._cancel_preparation = None

    def _on_entry_opened(self, index: int, start_frame: int | None, keep_shown: bool, media: EntryMedia) -> None:
        keep_focus = self._holds_focus()
        self._hide_loading_indicator()
        error = media.error
        runtime: OfflineSession | None = None
        if error is None:
            try:
                runtime = OfflineSession.build(
                    self,
                    media,
                    render_catalog_manager=self._render_catalog_manager,
                    use_lane_controls=len(self._navigation.get_entries()) == 1,
                    lane_visibility=self._lane_visibility,
                    pane_title=self.get_display_name(),
                )
            except Exception as exc:
                logger.exception(f"Failed to build displays for entry {index + 1}")
                error = str(exc)
        if runtime is None:
            self._pending_lane_focus = None
            release_media(media)
            message = f"Could not {'reload' if keep_shown else 'load'} entry {index + 1}: {error}"
            if keep_shown and self._reload_offered():
                logger.warning(message)  # The source changed again meanwhile: keep the offer to reload that data.
            else:
                self.show_warning(message)
            return
        playing = False
        previous = self._runtime
        if previous is not None:
            # Only a reload finds an entry still shown: the new one takes over where it is.
            start_frame, playing = previous.playback.current_frame, previous.playback.is_playing
            self._teardown_runtime()
        self._install_entry(runtime, start_frame)
        if playing:
            self._start_playback()
        if self._pending_lane_focus is not None:
            _, lane_index = self._pending_lane_focus
            self.focus_lane(lane_index)
        elif keep_focus and runtime.lanes:
            runtime.focus_lane(runtime.lanes[0].lane_index)

    def _holds_focus(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is self or (focus is not None and self.isAncestorOf(focus))

    def source_paths(self) -> SourcePaths:
        """Return overlay paths of the considered lanes on screen."""
        return self._navigation.overlay_paths(self._current_index)

    def reload(self) -> None:
        """Reload in the background, retaining the shown frame and playback on success or the old entry on failure.

        Zoom and media tools reset as for any entry change.
        """
        self._open_entry(self._current_index, keep_shown=True)

    def _show_loading_indicator(self) -> LoadingIndicator:
        if self._loading_indicator is None:
            self._loading_indicator = LoadingIndicator.cover(self._viewer_host or self)
        self._loading_indicator.set_message("")
        return self._loading_indicator

    def _hide_loading_indicator(self) -> None:
        indicator = self._loading_indicator
        if indicator is None:
            return
        self._loading_indicator = None
        indicator.dismiss()

    def _install_entry(self, runtime: OfflineSession, start_frame: int | None) -> None:
        self._runtime = runtime
        for lane in runtime.lanes:
            lane.display.contextMenuRequested.connect(partial(self._show_lane_menu, lane))

        if self._viewer_host is not None:
            self._viewer_host.set_content(runtime.container)
            runtime.container.show()
        entries = self._navigation.get_entries()
        self._transport.install(runtime, shared=runtime.has_multiple_lanes or len(entries) > 1)
        # Lanes of different videos have no single size or length to show.
        sources = runtime.iter_video_sources()
        self.set_header_details(video_details(sources[0]) if len(sources) == 1 else "")

        # The first frame shown is the one asked for, so a start frame never flashes frame 0 first.
        self._jump_to_frame(start_frame or 0)
        if start_frame is None:
            self._start_playback()
        self._prepare_next()

    def _show_lane_menu(self, lane: OfflineLane, global_pos: QPoint) -> None:
        """Show what can be done with *lane* at *global_pos*, after a right click on it has focused it.

        The rest of the menu is the window's actions, which act on the focused lane. Hide asks for the same change
        the sidebar's Hide does.
        """
        actions = [
            action for action_id in SHARED_ACTION_IDS if (action := self._shared_actions.get(action_id)) is not None
        ]
        ref = self._content.lane_consideration_ref(self._current_index, lane.lane_index)
        hideable = ref in {item.ref for item in self._content.consideration_items()}
        hide = partial(self.consideration_change_requested.emit, ref, False) if hideable else None
        exec_lane_menu(self.window(), lane, actions, hide, global_pos)

    def _start_playback(self) -> None:
        if self._runtime is not None:
            self._runtime.playback.play()

    def _pause_playback(self) -> None:
        if self._runtime is not None:
            self._runtime.playback.pause()

    def _jump_to_frame(self, frame_num: int) -> None:
        if self._runtime is not None:
            self._runtime.playback.seek(frame_num)

    def _step_frames(self, delta: int) -> None:
        if self._runtime is not None:
            self._runtime.playback.step(delta)

    def pause_playback(self) -> None:
        """Pause playback if currently playing."""
        runtime = self._runtime
        if runtime is not None and runtime.playback.is_playing:
            runtime.playback.pause()

    def export_video(self) -> None:
        """Export the shown entry, letting the user choose which lanes to include."""
        if self._runtime is not None:
            self._runtime.export_lanes()

    def toggle_playback(self) -> None:
        """Toggle between playing and paused states."""
        runtime = self._runtime
        if runtime is None:
            return
        runtime.playback.toggle()

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
        self._transport.set_speed(speed, self._runtime)

    def increase_playback_speed(self) -> None:
        """Increase playback speed by one step for shortcut routing."""
        self.set_playback_speed(step_playback_speed(self._transport.speed, 1, floor=0.1))

    def decrease_playback_speed(self) -> None:
        """Decrease playback speed by one step for shortcut routing."""
        self.set_playback_speed(step_playback_speed(self._transport.speed, -1, floor=0.1))

    def _update_nav_state(self) -> None:
        if self._entry_bar is not None:
            navigation = self._navigation
            self._entry_bar.show_position(
                self._current_index,
                len(navigation.get_entries()),
                can_previous=navigation.find_prev_considered_index(self._current_index) is not None,
                can_next=navigation.find_next_considered_index(self._current_index) is not None,
            )

    def _step_prev(self) -> None:
        previous_index = self._navigation.find_prev_considered_index(self._current_index)
        if previous_index is not None:
            self._open_entry(previous_index)

    def _step_next(self) -> None:
        next_index = self._navigation.find_next_considered_index(self._current_index)
        if next_index is not None:
            self._open_entry(next_index)

    def _teardown_runtime(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        self._runtime = None
        self.set_header_details("")
        runtime.cleanup()

    def get_display_name(self) -> str:
        return self._display_name

    def set_display_name(self, name: str) -> None:
        """Show *name* in the header and on the next pane title, after the item is renamed."""
        self._display_name = name
        super().set_display_name(name)

    def current_on_screen_item(self) -> OnScreenWorkspaceItem:
        """Return the workspace item shown by this viewer."""
        return self._content.on_screen_item(self._current_index)

    def bookmark_target(self) -> BookmarkTarget:
        """Return the target of the entry shown."""
        return entry_target(self._content, self._current_index)

    def set_active(self, active: bool) -> None:
        """Cancel deferred lane focus when another pane becomes active."""
        super().set_active(active)
        if not active:
            self._pending_lane_focus = None

    def focus_lane(self, lane_index: int) -> None:
        """Focus a visible lane now, or when the current entry finishes opening; never seek or restart it."""
        if self._is_cleaned_up:
            return
        self._pending_lane_focus = (self._current_index, lane_index)
        if self._runtime is not None and self._runtime.focus_lane(lane_index):
            self._pending_lane_focus = None

    def show_frame(self, frame: int) -> None:
        """Show *frame* of the entry shown, paused; an entry still opening opens at it instead."""
        if self._runtime is None:
            self._open_entry(self._current_index, frame)
            return
        self.pause_playback()
        self._jump_to_frame(frame)

    def bookmark_position(self) -> BookmarkPosition | None:
        """Return the entry and frame on screen, or None before the entry shows its first frame."""
        presented = self._runtime.playback.presented_frame if self._runtime is not None else None
        if presented is None:
            return None
        return BookmarkPosition(self._content, self._current_index, presented.frame, presented.seconds)

    def playhead_frame(self) -> int:
        """Return the runtime playhead or the frame requested while this entry is still opening."""
        return self._runtime.playback.current_frame if self._runtime is not None else (self._start_frame or 0)

    def get_contents(self) -> list[SeekableVideoContent]:
        return self._navigation.videos(self._current_index)

    def cleanup(self) -> None:
        if self._is_cleaned_up:
            return
        self._is_cleaned_up = True
        self._opening.close()
        self._opening_jobs.close()
        self._stop_preparation()
        self._hide_loading_indicator()
        self._teardown_runtime()
        self._entry_bar = None
        self._transport.cleanup()

    def refresh_item_consideration(self, item_ref: ConsiderationItemRef, _considered: bool) -> None:
        """Refresh viewer state after an item consideration change."""
        if self._is_cleaned_up:
            return
        content = self._content
        if item_ref.item_id != content.item_id or item_ref.entry not in content.bookmark_entries:
            return

        is_current = item_ref.entry == content.bookmark_entries[self._current_index]
        if is_current and item_ref.lane_index is None:
            if self._navigation.is_entry_considered(self._current_index):
                self._update_nav_state()
                return
            replacement_index = self._navigation.replacement_index_for_current_entry(self._current_index)
            if replacement_index is not None:
                self._open_entry(replacement_index)
            else:
                self._update_nav_state()
            return

        self._update_nav_state()
        if is_current:
            self._open_entry(self._current_index)
        elif self._runtime is not None:
            self._prepare_next()
