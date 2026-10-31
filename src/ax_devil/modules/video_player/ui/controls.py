"""Playback controls that fade in over the video: a thin timeline above one compact row of controls."""

from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from ax_devil.core.playback_speed import (
    DEFAULT_PLAYBACK_SPEED,
    clamp_playback_speed,
    format_playback_speed,
)
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Space, TextRole

from .control_panel import BaseVideoControlPanel, video_control_icon
from .timeline_slider import ClickableSlider, TimelineSliderPainter

SPEED_PRESETS = (0.25, 0.5, 1.0, 1.5, 2.0, 4.0)
_CACHED_RANGES_POLL_MS = 500


_READOUT_STYLE = """
    QAbstractSpinBox, QLabel {
        background-color: transparent;
        border: 0px;
        color: white;
        padding: 0px;
        min-height: 0px;
    }
"""


def format_timecode(seconds: float, *, hundredths: bool = True) -> str:
    """Return *seconds* as ``m:ss.cc``, or ``h:mm:ss.cc`` from one hour; without *hundredths*, as ``m:ss``."""
    total = max(0, round(seconds * 100)) if hundredths else max(0, round(seconds)) * 100
    minutes, remainder = divmod(total, 6000)
    hours, minutes = divmod(minutes, 60)
    second_text = f"{remainder // 100:02d}.{remainder % 100:02d}" if hundredths else f"{remainder // 100:02d}"
    return f"{hours}:{minutes:02d}:{second_text}" if hours else f"{minutes}:{second_text}"


class SeekableVideoControlPanel(BaseVideoControlPanel):
    """Video control panel for recorded videos: timeline, frame steps, frame number, time and speed."""

    frameStepRequested = QtCore.Signal(int)
    jumpToRequested = QtCore.Signal(int)
    scrubStarted = QtCore.Signal()
    scrubFinished = QtCore.Signal()
    playbackSpeedChanged = QtCore.Signal(float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_is_scrubbing = False
        self._frame_rate = 0.0
        self._duration_s = 0.0
        self._shown_seconds: float | None = None  # The decoded time of the frame on screen, when known.
        self._frame_seconds: Callable[[int], float | None] | None = None
        self._playback_speed = DEFAULT_PLAYBACK_SPEED
        self._cached_ranges_provider: Callable[[], tuple[tuple[int, int], ...]] | None = None
        self._cached_ranges_timer = QtCore.QTimer(self)
        self._cached_ranges_timer.setInterval(_CACHED_RANGES_POLL_MS)
        self._cached_ranges_timer.timeout.connect(self._refresh_cached_ranges)

        timeline_layout = QtWidgets.QHBoxLayout(self._timeline_container)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(0)

        self.timeline_slider = ClickableSlider(QtCore.Qt.Orientation.Horizontal, self)
        self.timeline_slider.setFixedHeight(TimelineSliderPainter.minimum_height())
        self.timeline_slider.setAccessibleName("Timeline")
        self.timeline_slider.setAutoFillBackground(False)
        self.timeline_slider.valueChanged.connect(self._on_slider_changed)
        self.timeline_slider.sliderPressed.connect(self._on_scrub_started)
        self.timeline_slider.sliderReleased.connect(self._on_scrub_finished)
        timeline_layout.addWidget(self.timeline_slider)

        # Ten-frame steps stay on the arrow keys; the row keeps the single-frame steps.
        for icon, tooltip, delta in (
            (Icon.STEP_BACK, "Back 1 frame", -1),
            (Icon.STEP_FORWARD, "Forward 1 frame", 1),
        ):
            btn = self._icon_button()
            btn.setIcon(video_control_icon(icon))
            btn.setToolTip(tooltip)
            btn.setAccessibleName(tooltip)
            btn.clicked.connect(lambda checked=False, d=delta: self.frameStepRequested.emit(d))
            self._container_layout.addWidget(btn)

        self._context_widget: QtWidgets.QWidget | None = None
        self._context_index = self._container_layout.count()
        self._container_layout.addStretch(1)

        self._frame_readout = QtWidgets.QWidget(self._controls_container)
        frame_layout = QtWidgets.QHBoxLayout(self._frame_readout)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        self.frame_spinbox = QtWidgets.QSpinBox(self._frame_readout)
        self.frame_spinbox.setAutoFillBackground(False)
        self.frame_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.frame_spinbox.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.frame_spinbox.setMinimum(0)
        self.frame_spinbox.setStyleSheet(_READOUT_STYLE)
        self.frame_spinbox.setToolTip("Frame")
        self.frame_spinbox.setAccessibleName("Frame")
        self.frame_spinbox.editingFinished.connect(self._on_spinbox_editing_finished)
        frame_layout.addWidget(self.frame_spinbox)

        self._total_label = QtWidgets.QLabel(" / 0", self._frame_readout)
        self._total_label.setStyleSheet(_READOUT_STYLE)
        frame_layout.addWidget(self._total_label)
        self._container_layout.addWidget(self._frame_readout)

        self._timecode_label = QtWidgets.QLabel(self._controls_container)
        self._timecode_label.setStyleSheet(f"{_READOUT_STYLE} QLabel {{ padding: 0px {Space.XS}px 0px {Space.L}px; }}")
        self._timecode_label.setToolTip("Time")
        self._container_layout.addWidget(self._timecode_label)

        self.speed_button = QtWidgets.QToolButton(self._controls_container)
        self.speed_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.speed_button.setToolTip("Playback speed")
        self.speed_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._speed_menu = QtWidgets.QMenu(self.speed_button)
        speed_group = QtGui.QActionGroup(self._speed_menu)
        self._speed_actions: dict[float, QtGui.QAction] = {}
        for speed in SPEED_PRESETS:
            action = self._speed_menu.addAction(format_playback_speed(speed))
            action.setCheckable(True)
            action.setActionGroup(speed_group)
            action.triggered.connect(lambda checked=False, value=speed: self.set_playback_speed(value))
            self._speed_actions[speed] = action
        self.speed_button.setMenu(self._speed_menu)
        self._container_layout.addWidget(self.speed_button)
        self._show_playback_speed()

        # When the row is narrow, the time hides first, then the speed, then the frame number.
        for widget in (self._timecode_label, self.speed_button, self._frame_readout):
            self._add_optional(widget)
        self._set_available(self._timecode_label, False)

        def apply_readout_fonts() -> None:
            for widget in (self.frame_spinbox, self._total_label, self._timecode_label):
                widget.setFont(TextRole.MONO.font())
            self._size_frame_spinbox()
            self._size_timecode()

        follow_appearance(self._frame_readout, apply_readout_fonts)
        self._apply_appearance()

    def set_context_widget(self, widget: QtWidgets.QWidget | None) -> None:
        """Place caller-owned transport context between frame stepping and frame status."""
        if self._context_widget is widget:
            return
        if self._context_widget is not None:
            self._container_layout.removeWidget(self._context_widget)
            self._context_widget.setParent(None)
        else:
            spacer = self._container_layout.takeAt(self._context_index)
            if spacer is not None:
                del spacer
        self._context_widget = widget
        if widget is not None:
            self._container_layout.insertWidget(self._context_index, widget, 1)
        else:
            self._container_layout.insertStretch(self._context_index, 1)
        self._apply_appearance()

    def show_video(
        self,
        total_frames: int,
        frame_rate: float,
        cached_ranges: Callable[[], tuple[tuple[int, int], ...]] | None = None,
        duration_s: float | None = None,
        frame_seconds: Callable[[int], float | None] | None = None,
    ) -> None:
        """Follow a video of *total_frames* at *frame_rate* frames per second; zero or less hides the time.

        *duration_s* is the video's length, which sizes the time readout; without it, frames count at *frame_rate*.
        *frame_seconds* looks up a frame's own time without waiting, for the frame picked while scrubbing; when it has
        no answer, the time counts frames at *frame_rate* until the frame is shown.
        *cached_ranges* reports the decoded frames as inclusive ``(first, last)`` runs; the timeline shows them,
        refreshed while the panel is visible.
        """
        self.set_total_frames(total_frames)
        self._frame_rate = frame_rate
        self._duration_s = (
            duration_s if duration_s is not None else total_frames / frame_rate if frame_rate > 0 else 0.0
        )
        self._shown_seconds = None
        self._frame_seconds = frame_seconds
        self._size_timecode()
        self._show_timecode()
        self._set_available(self._timecode_label, frame_rate > 0)
        self._cached_ranges_provider = cached_ranges
        self._refresh_cached_ranges()
        if cached_ranges is None:
            self._cached_ranges_timer.stop()
        else:
            self._cached_ranges_timer.start()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        """Show the current cache at once instead of on the next poll."""
        super().showEvent(event)
        self._refresh_cached_ranges()

    def _refresh_cached_ranges(self) -> None:
        if self._cached_ranges_provider is None:
            self.timeline_slider.set_cached_ranges(())
        elif self.isVisible():
            self.timeline_slider.set_cached_ranges(self._cached_ranges_provider())

    def timecode_text(self) -> str:
        """Return the shown time of the current frame, or an empty string when the frame rate is unknown."""
        return self._timecode_label.text()

    def _show_timecode(self) -> None:
        """Show the decoded time of the frame on screen, or, while scrubbing, the frame number at the frame rate."""
        if self._frame_rate > 0:
            seconds = self._shown_seconds
            if seconds is None:
                seconds = self.frame_spinbox.value() / self._frame_rate
            self._timecode_label.setText(format_timecode(seconds))

    def _size_timecode(self) -> None:
        """Fit the time to its longest text for this video, so it never grows into the row while playing."""
        longest = format_timecode(self._duration_s)
        # The time is fixed-width text, so the longest time is as wide as any other of its length.
        width = self._timecode_label.fontMetrics().horizontalAdvance(longest)
        self._timecode_label.setMinimumWidth(width + Space.L + Space.XS)
        self._fit_row()

    def _size_frame_spinbox(self) -> None:
        """Fit the frame field to the largest frame number, so it sits right next to the total."""
        digits = len(str(self.frame_spinbox.maximum()))
        self.frame_spinbox.setFixedWidth(self.frame_spinbox.fontMetrics().horizontalAdvance("0" * digits) + Space.S)
        self._fit_row()

    def _show_playback_speed(self) -> None:
        self.speed_button.setText(format_playback_speed(self._playback_speed))
        for speed, action in self._speed_actions.items():
            action.setChecked(abs(speed - self._playback_speed) <= 1e-9)

    def playback_speed(self) -> float:
        return self._playback_speed

    def set_playback_speed(self, speed: float, *, emit_signals: bool = True) -> None:
        clamped = clamp_playback_speed(speed)
        if abs(clamped - self._playback_speed) <= 1e-9:
            self._show_playback_speed()
            return

        self._playback_speed = clamped
        self._show_playback_speed()
        self._fit_row()
        if emit_signals:
            self.playbackSpeedChanged.emit(clamped)

    def _on_scrub_started(self) -> None:
        self._user_is_scrubbing = True
        self.scrubStarted.emit()

    def _on_scrub_finished(self) -> None:
        self._user_is_scrubbing = False
        self.jumpToRequested.emit(self.timeline_slider.value())
        self.scrubFinished.emit()

    def _on_slider_changed(self, value: int) -> None:
        if self.timeline_slider.value() != self.frame_spinbox.value():
            self.frame_spinbox.setValue(value)
        self._shown_seconds = self._frame_seconds(value) if self._frame_seconds is not None else None
        self._show_timecode()

    def _on_spinbox_editing_finished(self) -> None:
        new_value = self.frame_spinbox.value()
        if self.timeline_slider.value() != new_value:
            self.timeline_slider.setValue(new_value)
            self.jumpToRequested.emit(new_value)

    def set_total_frames(self, total: int) -> None:
        # 0-based indexing: valid range is [0, total-1]
        self.timeline_slider.setMinimum(0)
        self.timeline_slider.setMaximum(max(0, total - 1))
        self.frame_spinbox.setMaximum(max(0, total - 1))
        self._total_label.setText(f" / {max(0, total - 1)}")
        self._size_frame_spinbox()

    def set_current_frame(self, frame: int, seconds: float | None = None) -> None:
        """Show *frame* as current; *seconds* is its decoded time, which variable frame rates need to show truly."""
        if self._user_is_scrubbing:
            return

        self._shown_seconds = seconds
        self.timeline_slider.blockSignals(True)
        self.frame_spinbox.blockSignals(True)
        self.timeline_slider.setValue(frame)
        self.frame_spinbox.setValue(frame)
        self.timeline_slider.blockSignals(False)
        self.frame_spinbox.blockSignals(False)
        self._show_timecode()

    def cleanup(self) -> None:
        """Stop following the cache before the panel is torn down."""
        self._cached_ranges_timer.stop()
        self._cached_ranges_provider = None
        super().cleanup()
