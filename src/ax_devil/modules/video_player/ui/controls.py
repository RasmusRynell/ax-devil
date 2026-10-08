"""Playback controls that fade in over the video: a thin timeline above one compact row of controls."""

from collections.abc import Callable
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ax_devil.core.playback_speed import (
    DEFAULT_PLAYBACK_SPEED,
    clamp_playback_speed,
    format_playback_speed,
)
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole
from ax_devil.modules.settings.logging_config import get_logger

from ..constants import (
    CONTROL_PANEL_PADDING,
    DEFAULT_AUTO_HIDE_DELAY,
    DEFAULT_FADE_DURATION,
    TIMELINE_CACHED_ALPHA,
    TIMELINE_GROOVE_HEIGHT,
    TIMELINE_GROOVE_HOVER_HEIGHT,
    TIMELINE_HANDLE_RADIUS,
    TIMELINE_PROGRESS_ALPHA_DISABLED,
    TIMELINE_PROGRESS_ALPHA_ENABLED,
    TIMELINE_TRACK_ALPHA_DISABLED,
    TIMELINE_TRACK_ALPHA_ENABLED,
)
from .fading import FadingWidget

logger = get_logger(__name__)

SPEED_PRESETS = (0.25, 0.5, 1.0, 1.5, 2.0, 4.0)
_ICON_PX = 16
_SCRIM_ICON_STROKE = 2.0
_CACHED_RANGES_POLL_MS = 500


# White controls on the dark video scrim. Fonts are set on the widgets, so they follow the text size.
_BUTTON_STYLE = f"""
    QPushButton, QToolButton {{
        background-color: transparent;
        border: 0px;
        border-radius: {Radius.CONTROL}px;
        color: white;
        padding: {Space.XS}px {Space.S}px;
        min-height: 0px;
    }}
    QPushButton:hover, QToolButton:hover {{ background-color: rgba(255, 255, 255, 36); }}
    QPushButton:pressed, QToolButton:pressed {{ background-color: rgba(255, 255, 255, 64); }}
    QToolButton::menu-indicator {{ image: none; width: 0px; }}
    QLabel {{ color: white; }}
"""

_READOUT_STYLE = """
    QAbstractSpinBox, QLabel {
        background-color: transparent;
        border: 0px;
        color: white;
        padding: 0px;
        min-height: 0px;
    }
"""


def video_control_icon(icon: Icon) -> QtGui.QIcon:
    """Return a white playback icon for controls on the dark video scrim."""
    return icon.icon(QtGui.QColor(QtCore.Qt.GlobalColor.white), stroke=_SCRIM_ICON_STROKE)


def format_timecode(seconds: float, *, hundredths: bool = True) -> str:
    """Return *seconds* as ``m:ss.cc``, or ``h:mm:ss.cc`` from one hour; without *hundredths*, as ``m:ss``."""
    total = max(0, round(seconds * 100)) if hundredths else max(0, round(seconds)) * 100
    minutes, remainder = divmod(total, 6000)
    hours, minutes = divmod(minutes, 60)
    second_text = f"{remainder // 100:02d}.{remainder % 100:02d}" if hundredths else f"{remainder // 100:02d}"
    return f"{hours}:{minutes:02d}:{second_text}" if hours else f"{minutes}:{second_text}"


class TimelineSliderPainter:
    """Helper that renders a minimal, transparent horizontal timeline slider."""

    @staticmethod
    def minimum_height() -> int:
        """Return the smallest height that keeps the handle fully visible."""
        return TIMELINE_HANDLE_RADIUS * 2 + 2

    @staticmethod
    def paint_horizontal(
        slider: QtWidgets.QSlider,
        option: QtWidgets.QStyleOptionSlider,
        painter: QtGui.QPainter,
        *,
        hovered: bool,
        cached_ranges: tuple[tuple[int, int], ...],
    ) -> None:
        """Paint the track, the cached frames, the played part and, while hovered or dragged, the handle."""
        groove_height = TIMELINE_GROOVE_HOVER_HEIGHT if hovered else TIMELINE_GROOVE_HEIGHT
        groove_rect = QtCore.QRectF(
            TIMELINE_HANDLE_RADIUS,
            (slider.height() - groove_height) / 2,
            max(0, slider.width() - 2 * TIMELINE_HANDLE_RADIUS),
            groove_height,
        )
        radius = groove_height / 2
        is_enabled = bool(option.state & QtWidgets.QStyle.StateFlag.State_Enabled)
        track_alpha = TIMELINE_TRACK_ALPHA_ENABLED if is_enabled else TIMELINE_TRACK_ALPHA_DISABLED
        painter.setBrush(QtGui.QColor(255, 255, 255, track_alpha))
        painter.drawRoundedRect(groove_rect, radius, radius)

        frames = option.maximum - option.minimum + 1
        handle_x = groove_rect.left()
        if groove_rect.width() > 0 and frames > 1:
            frame_width = groove_rect.width() / frames
            painter.setBrush(QtGui.QColor(255, 255, 255, TIMELINE_CACHED_ALPHA))
            for first, last in cached_ranges:
                left = groove_rect.left() + (first - option.minimum) * frame_width
                width = (last - first + 1) * frame_width
                painter.drawRect(QtCore.QRectF(left, groove_rect.top(), width, groove_rect.height()))

            progress = (option.sliderPosition - option.minimum) / (frames - 1)
            handle_x = groove_rect.left() + progress * groove_rect.width()
            progress_alpha = TIMELINE_PROGRESS_ALPHA_ENABLED if is_enabled else TIMELINE_PROGRESS_ALPHA_DISABLED
            painter.setBrush(QtGui.QColor(255, 255, 255, progress_alpha))
            played = QtCore.QRectF(groove_rect.topLeft(), QtCore.QPointF(handle_x, groove_rect.bottom()))
            painter.drawRoundedRect(played, radius, radius)

        if hovered:
            painter.setBrush(QtGui.QColor(255, 255, 255))
            painter.drawEllipse(
                QtCore.QPointF(handle_x, groove_rect.center().y()), TIMELINE_HANDLE_RADIUS, TIMELINE_HANDLE_RADIUS
            )


class ClickableSlider(QtWidgets.QSlider):
    """Timeline slider that supports click-to-jump anywhere on the track and shows which frames are cached."""

    def __init__(
        self,
        orientation: QtCore.Qt.Orientation,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(orientation, parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)
        self.setStyleSheet(
            """
            QSlider {
                background: transparent;
                border: 0px;
            }
            """
        )
        self.setMinimumHeight(TimelineSliderPainter.minimum_height())
        self._cached_ranges: tuple[tuple[int, int], ...] = ()

    def set_cached_ranges(self, ranges: tuple[tuple[int, int], ...]) -> None:
        """Show *ranges*, inclusive ``(first, last)`` frame runs, as cached on the track."""
        if ranges != self._cached_ranges:
            self._cached_ranges = ranges
            self.update()

    def cached_ranges(self) -> tuple[tuple[int, int], ...]:
        """Return the frame runs shown as cached."""
        return self._cached_ranges

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """Handle mouse press to enable click-to-jump functionality."""
        if event is None or event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        if self.orientation() == QtCore.Qt.Orientation.Horizontal:
            self._jump_to_click_position(event.position().x())

        super().mousePressEvent(event)

    def _jump_to_click_position(self, click_x: float) -> None:
        """Jump to the frame under *click_x* on the painted track."""
        usable_width = self.width() - 2 * TIMELINE_HANDLE_RADIUS
        if usable_width > 0:
            relative_pos = max(0.0, min(1.0, (click_x - TIMELINE_HANDLE_RADIUS) / usable_width))
            self.setValue(round(self.minimum() + relative_pos * (self.maximum() - self.minimum())))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        """Paint a thin track that thickens and shows its handle while the pointer is over it."""
        if self.orientation() != QtCore.Qt.Orientation.Horizontal:
            super().paintEvent(event)
            return

        option = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(option)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        try:
            TimelineSliderPainter.paint_horizontal(
                self,
                option,
                painter,
                hovered=self.underMouse() or self.isSliderDown(),
                cached_ranges=self._cached_ranges,
            )
        finally:
            painter.end()


class BaseVideoControlPanel(FadingWidget):
    """Base video control panel with play/pause functionality and fading overlay support.

    The panel is a timeline over one compact row of controls on a dark scrim. When the row is too narrow, the
    widgets registered with ``_add_optional`` hide in that order until the rest fits.
    """

    playRequested = QtCore.Signal()
    pauseRequested = QtCore.Signal()
    stateChanged = QtCore.Signal(bool)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        # Initialize FadingWidget with control panel-specific settings
        super().__init__(
            parent=parent,
            fade_duration=DEFAULT_FADE_DURATION,
            auto_hide_delay=DEFAULT_AUTO_HIDE_DELAY,
            initial_opacity=0.0,
            hover_element_name="control_panel",
        )
        self._playing = False
        self._signal_guard = False
        self._optional_widgets: list[QtWidgets.QWidget] = []
        self._unavailable_widgets: set[QtWidgets.QWidget] = set()
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

        main_widget_layout = QtWidgets.QVBoxLayout(self)
        main_widget_layout.setContentsMargins(0, 0, 0, 0)
        main_widget_layout.setSpacing(0)

        # The scrim darkens whatever is behind the controls, so white text stays readable on any video.
        self._styled_container = QtWidgets.QWidget(self)
        self._styled_container.setObjectName("control_background_container")
        self._styled_container.setStyleSheet("""
            #control_background_container {
                background: qlineargradient(x1:0, y1:1, x2:0, y2:0,
                    stop:0 rgba(0, 0, 0, 200),
                    stop:0.55 rgba(0, 0, 0, 150),
                    stop:1 rgba(0, 0, 0, 0));
            }
        """)
        main_widget_layout.addWidget(self._styled_container)

        self._main_layout = QtWidgets.QVBoxLayout(self._styled_container)
        self._main_layout.setContentsMargins(CONTROL_PANEL_PADDING, 0, CONTROL_PANEL_PADDING, Space.XS)
        self._main_layout.setSpacing(0)
        self._main_layout.addStretch(1)

        # Timeline container (populated by SeekableVideoControlPanel)
        self._timeline_container = QtWidgets.QWidget(self._styled_container)
        self._timeline_container.setAutoFillBackground(False)
        self._timeline_container.setFixedHeight(TimelineSliderPainter.minimum_height())
        self._timeline_container.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._timeline_container.setStyleSheet("background: transparent; border: 0px;")
        self._main_layout.addWidget(self._timeline_container)

        self._controls_container = QtWidgets.QWidget(self._styled_container)
        self._controls_container.setObjectName("playbackControlsRow")
        self._controls_container.setAutoFillBackground(False)
        self._controls_container.setStyleSheet(
            # Scoped by name, so the speed menu that pops up from the row keeps the theme's surface.
            f"#playbackControlsRow {{ background-color: transparent; border: 0px; }}{_BUTTON_STYLE}"
        )
        TextRole.STRONG.apply(self._controls_container)

        self._container_layout = QtWidgets.QHBoxLayout(self._controls_container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(Space.XS)
        self._main_layout.addWidget(self._controls_container)

        self.play_pause_btn = self._icon_button()
        self.play_pause_btn.clicked.connect(self._on_play_pause_clicked)
        self._container_layout.addWidget(self.play_pause_btn)
        self._update_play_button()
        follow_appearance(self, self._apply_appearance)
        _id = id(self)
        _cls = self.__class__.__name__
        self.destroyed.connect(lambda _=None, _id=_id, _cls=_cls: logger.debug(f"{_cls}.destroyed id={_id}"))

    def _icon_button(self) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(self._controls_container)
        button.setAutoFillBackground(False)
        button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        button.setIconSize(QtCore.QSize(_ICON_PX, _ICON_PX))
        return button

    def _apply_appearance(self) -> None:
        """Size the row to one control height and leave room above the timeline for the scrim to fade in."""
        row_height = Height.CONTROL.px
        self._controls_container.setFixedHeight(row_height)
        for button in self._controls_container.findChildren(QtWidgets.QAbstractButton):
            button.setFixedHeight(row_height)
            if not button.text():
                button.setFixedWidth(row_height + Space.XS)
        self.setFixedHeight(Space.XL + TimelineSliderPainter.minimum_height() + row_height + Space.XS)
        self._fit_row()

    def _add_optional(self, widget: QtWidgets.QWidget) -> None:
        """Let *widget* hide when the row is too narrow; widgets added earlier hide first."""
        self._optional_widgets.append(widget)

    def _set_available(self, widget: QtWidgets.QWidget, available: bool) -> None:
        """Show or hide *widget* for content reasons, independently of the room the row has."""
        if available:
            self._unavailable_widgets.discard(widget)
        else:
            self._unavailable_widgets.add(widget)
        self._fit_row()

    def _row_width(self, widgets: list[QtWidgets.QWidget]) -> int:
        """Return the smallest row width that fits *widgets*, measured now rather than from the layout's cache."""
        widths = [widget.minimumSizeHint().expandedTo(widget.minimumSize()).width() for widget in widgets]
        return sum(widths) + self._container_layout.spacing() * max(0, len(widths) - 1)

    def _row_widgets(self) -> list[QtWidgets.QWidget]:
        layout = self._container_layout
        items = (layout.itemAt(index) for index in range(layout.count()))
        widgets = (item.widget() for item in items if item is not None)
        return [widget for widget in widgets if widget is not None and not widget.isHidden()]

    def _fit_row(self) -> None:
        """Show every available optional control that fits, hiding the least important ones first."""
        available_width = self.width() - 2 * CONTROL_PANEL_PADDING
        for widget in self._optional_widgets:
            widget.setVisible(widget not in self._unavailable_widgets)
        for widget in self._optional_widgets:
            if self._row_width(self._row_widgets()) <= available_width:
                break
            widget.setVisible(False)
        # Lay the row out now with its children's current sizes; Qt would otherwise reuse sizes from before a text or
        # font change until a later resize.
        for widget in self._row_widgets():
            child_layout = widget.layout()
            if child_layout is not None:
                child_layout.activate()
            widget.updateGeometry()
        self._container_layout.invalidate()
        self._container_layout.activate()
        self.updateGeometry()

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802
        """Return the size of the controls that always show, so a narrow pane hides the optional ones instead."""
        required = [widget for widget in self._row_widgets() if widget not in self._optional_widgets]
        return QtCore.QSize(self._row_width(required) + 2 * CONTROL_PANEL_PADDING, super().minimumSizeHint().height())

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """Refit the row to the new width."""
        super().resizeEvent(event)
        self._fit_row()

    def _on_play_pause_clicked(self) -> None:
        if not self._signal_guard:
            self.set_playing(not self._playing)

    def _update_play_button(self) -> None:
        self.play_pause_btn.setIcon(video_control_icon(Icon.PAUSE if self._playing else Icon.PLAY))
        self.play_pause_btn.setToolTip("Pause" if self._playing else "Play")

    def set_playing(self, playing: bool, *, emit_signals: bool = True) -> None:
        if playing == self._playing:
            return

        self._signal_guard = True
        self._playing = playing
        self._update_play_button()

        if emit_signals:
            (self.playRequested if playing else self.pauseRequested).emit()
            self.stateChanged.emit(playing)

        self._signal_guard = False

    def sync_playback_state(self, playing: bool) -> None:
        self.set_playing(playing, emit_signals=False)

    @property
    def is_playing(self) -> bool:
        return self._playing


class SeekableVideoControlPanel(BaseVideoControlPanel):
    """Video control panel for recorded videos: timeline, frame steps, frame number, time and speed."""

    frameStepRequested = QtCore.Signal(int)
    jumpToRequested = QtCore.Signal(int)
    scrubStarted = QtCore.Signal()
    scrubFinished = QtCore.Signal()
    playbackSpeedChanged = QtCore.Signal(float)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
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
        self._total_label.setText(f" / {total - 1}")
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
