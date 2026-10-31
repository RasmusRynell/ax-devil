"""The shared transport bar of the offline viewer, for playlists and multi-lane entries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from ax_devil.core.playback_speed import DEFAULT_PLAYBACK_SPEED, clamp_playback_speed
from ax_devil.modules.video_player.constants import MOUSE_IDLE_HIDE_DELAY_MS
from ax_devil.modules.video_player.orchestration.control_visibility import ControlVisibilityController
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineSession
from ax_devil.modules.video_viewer.offline_viewer_widgets import ViewerOverlayHost


@dataclass(frozen=True, slots=True)
class TransportCommands:
    """What a transport bar asks the viewer to do."""

    play: Callable[[], None]
    pause: Callable[[], None]
    jump_to: Callable[[int], None]
    step: Callable[[int], None]
    set_speed: Callable[[float], None]

    def connect(self, controls: SeekableVideoControlPanel) -> None:
        """Route the requests of *controls* to these commands."""
        controls.playRequested.connect(self.play)
        controls.pauseRequested.connect(self.pause)
        controls.jumpToRequested.connect(self.jump_to)
        controls.frameStepRequested.connect(self.step)
        controls.playbackSpeedChanged.connect(self.set_speed)


class OfflineTransport:
    """The playback speed and the transport bar shared by every lane of an entry.

    One lane of a one-entry playlist has no shared bar; its own controls do the work and this only keeps them in step.
    The bar and its visibility controller are parented to *viewer*.
    """

    def __init__(
        self,
        viewer: QWidget,
        commands: TransportCommands,
        *,
        context: QWidget | None,
        host: ViewerOverlayHost | None,
    ) -> None:
        # Dropped by cleanup: the viewer holds this transport, so keeping them would be a reference cycle that
        # outlives the viewer's C++ object.
        self._wiring: tuple[QWidget, TransportCommands] | None = (viewer, commands)
        self._context = context
        self._host = host
        self._panel: SeekableVideoControlPanel | None = None
        self._visibility: ControlVisibilityController | None = None
        self._shared = False
        self.speed = DEFAULT_PLAYBACK_SPEED

    @property
    def active(self) -> bool:
        """Whether the shared bar is the one in use."""
        return self._shared and self._panel is not None

    def install(self, runtime: OfflineSession, *, shared: bool) -> None:
        """Wire *runtime* to the transport: the shared bar when *shared*, else the entry's own controls."""
        if self._wiring is None:
            return
        viewer, commands = self._wiring
        self._use_shared(viewer, commands, shared)
        self._connect_visibility(runtime)

        controls = (
            self._panel
            if self.active
            else next(
                (panel for display in runtime.displays for panel in display.findChildren(SeekableVideoControlPanel)),
                None,
            )
        )
        if controls is not None:
            if not self.active:
                commands.connect(controls)
            runtime.playback.set_speed(self.speed)
            runtime.playback.follow(controls)

    def show_idle(self) -> None:
        """Show the shared bar with nothing to play, for a playlist entry that could not open, so stepping works.

        No lane is there to bring the bar back after it auto-hides, so it stays shown.
        """
        if self._wiring is None:
            return
        viewer, commands = self._wiring
        self._use_shared(viewer, commands, True)
        if self._panel is not None:
            self._panel.show_video(total_frames=0, frame_rate=0.0)
            self._panel.set_hover_sink(None)

    def set_speed(self, speed: float, runtime: OfflineSession | None) -> None:
        """Remember speed across entries and apply it to the active playback."""
        self.speed = clamp_playback_speed(speed)
        if runtime is not None:
            runtime.playback.set_speed(self.speed)
        elif self._panel is not None:
            self._panel.set_playback_speed(self.speed, emit_signals=False)

    def cleanup(self) -> None:
        """Release the bar and its visibility controller; the transport installs nothing afterwards."""
        self._wiring = None
        if self._visibility is not None:
            self._visibility.cleanup()
        self._visibility = None
        if self._panel is not None:
            self._panel.cleanup()
        self._panel = None

    def _use_shared(self, viewer: QWidget, commands: TransportCommands, shared: bool) -> None:
        self._shared = shared
        if not shared:
            if self._panel is not None:
                self._panel.sync_playback_state(False)
                self._panel.hide()
            return
        if self._panel is None:
            controls = SeekableVideoControlPanel(viewer)
            commands.connect(controls)
            controls.set_context_widget(self._context)
            self._panel = controls
            self._visibility = ControlVisibilityController(
                timer_parent=viewer,
                idle_hide_delay_ms=MOUSE_IDLE_HIDE_DELAY_MS,
                request_show=controls.fade_in,
                request_hide=controls.start_auto_hide_timer,
            )
            if self._host is not None:
                self._host.set_bottom_overlay(controls)
        self._panel.show()
        self._panel.raise_()
        self._panel.fade_in()
        self._panel.sync_playback_state(False)
        self._panel.set_playback_speed(self.speed, emit_signals=False)

    def _connect_visibility(self, runtime: OfflineSession) -> None:
        """Let pointer activity in any lane control the shared transport overlay."""
        if not self.active or self._panel is None or self._visibility is None:
            return
        self._panel.set_hover_sink(self._visibility)
        for display in runtime.displays:
            display.controlsRequested.connect(self._visibility.on_mouse_move)
            display.controlsHideRequested.connect(self._visibility.on_mouse_leave)
