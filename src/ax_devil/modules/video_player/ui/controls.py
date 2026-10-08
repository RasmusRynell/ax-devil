"""Video control panels for PySide6 applications.

Professional video control panels with semi-transparent styling and advanced UX features.
Enhanced with fading overlay functionality for floating controls.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from ax_devil.core.playback_speed import (
    DEFAULT_PLAYBACK_SPEED,
    MAX_PLAYBACK_SPEED,
    MIN_PLAYBACK_SPEED,
    clamp_playback_speed,
    format_playback_speed,
    step_playback_speed,
)
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.settings.logging_config import get_logger

from ..constants import (
    CONTROL_PANEL_HEIGHT,
    CONTROL_PANEL_PADDING,
    DEFAULT_AUTO_HIDE_DELAY,
    DEFAULT_FADE_DURATION,
    TIMELINE_GROOVE_HEIGHT,
    TIMELINE_HANDLE_RADIUS,
    TIMELINE_HEIGHT,
    TIMELINE_PROGRESS_ALPHA_DISABLED,
    TIMELINE_PROGRESS_ALPHA_ENABLED,
    TIMELINE_TRACK_ALPHA_DISABLED,
    TIMELINE_TRACK_ALPHA_ENABLED,
)
from .fading import FadingWidget

logger = get_logger(__name__)


# White controls on the dark video scrim. Fonts are set on the widgets, so they follow the text size.
def _button_style(horizontal_padding: int) -> str:
    return f"""
        QPushButton {{
            background-color: transparent;
            color: white;
            padding: {Space.M}px {horizontal_padding}px;
            min-width: {Space.XL + Space.S}px;
        }}
    """


_READOUT_STYLE = """
    QAbstractSpinBox, QLabel {
        background-color: transparent;
        border: 0px;
        color: white;
    }
"""


def video_control_icon(icon: Icon) -> QtGui.QIcon:
    """Return a white playback icon for controls on the dark video scrim."""
    return icon.icon(QtGui.QColor(QtCore.Qt.GlobalColor.white))


class TimelineSliderPainter:
    """Helper that renders a minimal, transparent horizontal timeline slider."""

    GROOVE_HEIGHT = TIMELINE_GROOVE_HEIGHT
    HANDLE_RADIUS = TIMELINE_HANDLE_RADIUS
    TRACK_ALPHA_ENABLED = TIMELINE_TRACK_ALPHA_ENABLED
    TRACK_ALPHA_DISABLED = TIMELINE_TRACK_ALPHA_DISABLED
    PROGRESS_ALPHA_ENABLED = TIMELINE_PROGRESS_ALPHA_ENABLED
    PROGRESS_ALPHA_DISABLED = TIMELINE_PROGRESS_ALPHA_DISABLED

    @classmethod
    def minimum_height(cls) -> int:
        """Return the smallest height that keeps the handle fully visible."""
        return (cls.HANDLE_RADIUS * 2) + cls.GROOVE_HEIGHT

    @classmethod
    def _effective_handle_radius(cls, slider_height: int) -> int:
        return min(cls.HANDLE_RADIUS, max(2, (slider_height // 2) - 1))

    @classmethod
    def paint_horizontal(
        cls,
        slider: QtWidgets.QSlider,
        option: QtWidgets.QStyleOptionSlider,
        painter: QtGui.QPainter,
    ) -> None:
        handle_radius = cls._effective_handle_radius(slider.height())
        horizontal_margin = handle_radius
        groove_rect = QtCore.QRect(
            horizontal_margin,
            (slider.height() - cls.GROOVE_HEIGHT) // 2,
            max(0, slider.width() - (2 * horizontal_margin)),
            cls.GROOVE_HEIGHT,
        )

        is_enabled = bool(option.state & QtWidgets.QStyle.StateFlag.State_Enabled)
        track_alpha = cls.TRACK_ALPHA_ENABLED if is_enabled else cls.TRACK_ALPHA_DISABLED
        painter.setBrush(QtGui.QColor(255, 255, 255, track_alpha))
        painter.drawRoundedRect(groove_rect, cls.GROOVE_HEIGHT / 2, cls.GROOVE_HEIGHT / 2)

        span = groove_rect.width()
        if span > 0 and option.maximum != option.minimum:
            progress_width = QtWidgets.QStyle.sliderPositionFromValue(
                option.minimum,
                option.maximum,
                option.sliderPosition,
                span,
                False,
            )
            progress_rect = QtCore.QRect(
                groove_rect.left(),
                groove_rect.top(),
                progress_width,
                groove_rect.height(),
            )
            progress_alpha = cls.PROGRESS_ALPHA_ENABLED if is_enabled else cls.PROGRESS_ALPHA_DISABLED
            painter.setBrush(QtGui.QColor(255, 255, 255, progress_alpha))
            painter.drawRoundedRect(progress_rect, cls.GROOVE_HEIGHT / 2, cls.GROOVE_HEIGHT / 2)
            handle_center_x = groove_rect.left() + progress_width
        else:
            handle_center_x = groove_rect.left()

        handle_rect = QtCore.QRect(
            handle_center_x - handle_radius,
            groove_rect.center().y() - handle_radius,
            handle_radius * 2,
            handle_radius * 2,
        )
        painter.setBrush(QtGui.QColor(255, 255, 255))
        painter.drawEllipse(handle_rect)


class ClickableSlider(QtWidgets.QSlider):
    """Timeline slider that supports click-to-jump anywhere on the track."""

    def __init__(
        self,
        orientation: QtCore.Qt.Orientation,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setStyleSheet(
            """
            QSlider {
                background: transparent;
                border: 0px;
            }
            """
        )
        self.setMinimumHeight(TimelineSliderPainter.minimum_height())

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """Handle mouse press to enable click-to-jump functionality."""
        if event is None or event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        if self.orientation() == QtCore.Qt.Orientation.Horizontal:
            self._jump_to_click_position(event.position().x())

        super().mousePressEvent(event)

    def _jump_to_click_position(self, click_x: float) -> None:
        """Calculate and jump to the clicked position on the timeline."""
        widget_style = self.style()
        if widget_style is None:
            return

        handle_width = widget_style.pixelMetric(QtWidgets.QStyle.PixelMetric.PM_SliderThickness)
        usable_width = self.width() - handle_width

        if usable_width > 0:
            relative_pos = max(0.0, min(1.0, (click_x - handle_width / 2) / usable_width))
            new_value = int(self.minimum() + relative_pos * (self.maximum() - self.minimum()))
            new_value = int(max(self.minimum(), min(self.maximum(), new_value)))
            self.setValue(new_value)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        """Custom paint to match YouTube-style transparent slider."""
        if self.orientation() != QtCore.Qt.Orientation.Horizontal:
            super().paintEvent(event)
            return

        option = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(option)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        try:
            TimelineSliderPainter.paint_horizontal(self, option, painter)
        finally:
            painter.end()


# Control panel styling is now handled by QDarkTheme automatically
# Semi-transparent overlay effect will be handled by paintEvent in base class
# All constants now imported from constants.py


class BaseVideoControlPanel(FadingWidget):
    """Base video control panel with play/pause functionality and fading overlay support."""

    playRequested = QtCore.Signal()
    pauseRequested = QtCore.Signal()
    stateChanged = QtCore.Signal(bool)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
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

        # YouTube-style control panel: full width with vertical stacking
        # Make the container bigger (80px instead of 50px)
        container_height = CONTROL_PANEL_HEIGHT + 30  # Add 30px for larger background
        self.setMaximumHeight(container_height)
        self.setMinimumHeight(container_height)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

        # Create a main layout for this widget (BaseVideoControlPanel)
        main_widget_layout = QtWidgets.QVBoxLayout(self)
        main_widget_layout.setContentsMargins(0, 0, 0, 0)
        main_widget_layout.setSpacing(0)

        # Create the styled container that will hold all controls
        self._styled_container = QtWidgets.QWidget(self)
        self._styled_container.setObjectName("control_background_container")
        self._styled_container.setStyleSheet("""
            #control_background_container {
                background: qlineargradient(x1:0, y1:1, x2:0, y2:0,
                    stop:0 rgba(0, 0, 0, 180),
                    stop:0.4 rgba(0, 0, 0, 130),
                    stop:0.7 rgba(0, 0, 0, 50),
                    stop:1 rgba(0, 0, 0, 0));
            }
        """)
        main_widget_layout.addWidget(self._styled_container)

        # Main vertical layout for two-row structure (now inside the styled container)
        self._main_layout = QtWidgets.QVBoxLayout(self._styled_container)
        self._main_layout.setContentsMargins(CONTROL_PANEL_PADDING, 0, CONTROL_PANEL_PADDING, CONTROL_PANEL_PADDING)
        self._main_layout.setSpacing(Space.S)  # Same gap between timeline and controls as between buttons

        # Add spacer at the top to push controls to the bottom
        self._main_layout.addStretch(1)

        # Timeline container (will be populated by SeekableVideoControlPanel)
        self._timeline_container = QtWidgets.QWidget(self._styled_container)
        self._timeline_container.setAutoFillBackground(False)
        initial_timeline_height = max(TIMELINE_HEIGHT, TimelineSliderPainter.minimum_height())
        self._timeline_container.setFixedHeight(initial_timeline_height)
        self._timeline_container.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._timeline_container.setStyleSheet("background: transparent; border: 0px;")
        self._main_layout.addWidget(self._timeline_container)

        # Controls container for buttons
        self._controls_container = QtWidgets.QWidget(self._styled_container)
        self._controls_container.setAutoFillBackground(False)
        self._controls_container.setStyleSheet("QWidget { background-color: transparent; border: 0px; }")
        TextRole.STRONG.apply(self._controls_container)

        self._container_layout = QtWidgets.QHBoxLayout(self._controls_container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(Space.S)
        self._main_layout.addWidget(self._controls_container)

        self.play_pause_btn = QtWidgets.QPushButton(self._controls_container)
        self.play_pause_btn.setAutoFillBackground(False)
        self.play_pause_btn.setStyleSheet(_button_style(Space.L))
        self.play_pause_btn.clicked.connect(self._on_play_pause_clicked)
        self._container_layout.addWidget(self.play_pause_btn)
        self._update_play_button()
        _id = id(self)
        _cls = self.__class__.__name__
        self.destroyed.connect(lambda _=None, _id=_id, _cls=_cls: logger.debug(f"{_cls}.destroyed id={_id}"))

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
    """Video control panel for recorded videos with timeline navigation."""

    frameStepRequested = QtCore.Signal(int)
    jumpToRequested = QtCore.Signal(int)
    scrubStarted = QtCore.Signal()
    scrubFinished = QtCore.Signal()
    playbackSpeedChanged = QtCore.Signal(float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_is_scrubbing = False

        # ===== TOP ROW: Timeline =====
        timeline_layout = QtWidgets.QHBoxLayout(self._timeline_container)
        timeline_layout.setContentsMargins(0, 0, 0, 0)  # Small top padding for timeline
        timeline_layout.setSpacing(0)

        # Timeline slider spans full width
        self.timeline_slider = ClickableSlider(QtCore.Qt.Orientation.Horizontal, self)
        timeline_height = max(TIMELINE_HEIGHT, TimelineSliderPainter.minimum_height())
        self.timeline_slider.setFixedHeight(timeline_height)
        self.timeline_slider.setAutoFillBackground(False)
        self.timeline_slider.valueChanged.connect(self._on_slider_changed)
        self.timeline_slider.sliderPressed.connect(self._on_scrub_started)
        self.timeline_slider.sliderReleased.connect(self._on_scrub_finished)

        timeline_layout.addWidget(self.timeline_slider)
        self._timeline_container.setFixedHeight(timeline_height)

        # ===== BOTTOM ROW: Controls =====
        # Navigation buttons
        steps = [
            (Icon.STEP_BACK_MANY, "Back 10 frames", -10),
            (Icon.STEP_BACK, "Back 1 frame", -1),
            (Icon.STEP_FORWARD, "Forward 1 frame", 1),
            (Icon.STEP_FORWARD_MANY, "Forward 10 frames", 10),
        ]
        for icon, tooltip, delta in steps:
            btn = QtWidgets.QPushButton(self)
            btn.setIcon(video_control_icon(icon))
            btn.setToolTip(tooltip)
            btn.setAccessibleName(tooltip)
            btn.setAutoFillBackground(False)
            btn.setStyleSheet(_button_style(Space.L))
            btn.clicked.connect(lambda checked=False, d=delta: self.frameStepRequested.emit(d))
            self._container_layout.addWidget(btn)

        self._context_widget: QtWidgets.QWidget | None = None

        # Add some spacing before time display
        self._container_layout.addStretch(1)

        # Frame spinbox and total label grouped together
        time_container = QtWidgets.QWidget()
        time_container.setAutoFillBackground(False)
        time_layout = QtWidgets.QHBoxLayout(time_container)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(0)

        self.frame_spinbox = QtWidgets.QSpinBox(self)
        self.frame_spinbox.setAutoFillBackground(False)
        self.frame_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.frame_spinbox.setMinimum(0)
        self.frame_spinbox.setStyleSheet(_READOUT_STYLE)
        TextRole.MONO.apply(self.frame_spinbox)
        self.frame_spinbox.editingFinished.connect(self._on_spinbox_editing_finished)
        time_layout.addWidget(self.frame_spinbox)

        self._total_label = QtWidgets.QLabel("/ Unknown", self)
        self._total_label.setStyleSheet(_READOUT_STYLE)
        TextRole.MONO.apply(self._total_label)
        time_layout.addWidget(self._total_label)

        self._container_layout.addWidget(time_container)

        # Playback speed controls
        speed_container = QtWidgets.QWidget(self)
        speed_container.setAutoFillBackground(False)
        speed_layout = QtWidgets.QHBoxLayout(speed_container)
        speed_layout.setContentsMargins(Space.S, 0, 0, 0)
        speed_layout.setSpacing(Space.XS)

        self.speed_down_btn = QtWidgets.QPushButton("-", self)
        self.speed_down_btn.setAutoFillBackground(False)
        self.speed_down_btn.setStyleSheet(_button_style(Space.M))
        self.speed_down_btn.clicked.connect(lambda: self.step_playback_speed(-1))
        speed_layout.addWidget(self.speed_down_btn)

        self.speed_spinbox = QtWidgets.QDoubleSpinBox(self)
        self.speed_spinbox.setAutoFillBackground(False)
        self.speed_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.speed_spinbox.setRange(MIN_PLAYBACK_SPEED, MAX_PLAYBACK_SPEED)
        self.speed_spinbox.setDecimals(2)
        self.speed_spinbox.setSingleStep(0.1)
        self.speed_spinbox.setSuffix("x")
        self.speed_spinbox.setValue(DEFAULT_PLAYBACK_SPEED)
        self.speed_spinbox.setStyleSheet(_READOUT_STYLE)

        def apply_speed_font() -> None:
            self.speed_spinbox.setFont(TextRole.MONO.font())
            self.speed_spinbox.setMinimumWidth(self.speed_spinbox.fontMetrics().horizontalAdvance("00.00x") + Space.M)

        follow_appearance(self.speed_spinbox, apply_speed_font)
        self.speed_spinbox.valueChanged.connect(self._on_speed_changed)
        speed_layout.addWidget(self.speed_spinbox)

        self.speed_up_btn = QtWidgets.QPushButton("+", self)
        self.speed_up_btn.setAutoFillBackground(False)
        self.speed_up_btn.setStyleSheet(_button_style(Space.M))
        self.speed_up_btn.clicked.connect(lambda: self.step_playback_speed(1))
        speed_layout.addWidget(self.speed_up_btn)
        self._container_layout.addWidget(speed_container)

    def set_context_widget(self, widget: QtWidgets.QWidget | None) -> None:
        """Place caller-owned transport context between frame stepping and frame status."""
        if self._context_widget is widget:
            return
        if self._context_widget is not None:
            self._container_layout.removeWidget(self._context_widget)
            self._context_widget.setParent(None)
        else:
            spacer = self._container_layout.takeAt(5)
            if spacer is not None:
                del spacer
        self._context_widget = widget
        if widget is not None:
            self._container_layout.insertWidget(5, widget, 1)
        else:
            self._container_layout.insertStretch(5, 1)

    def _on_speed_changed(self, value: float) -> None:
        clamped = clamp_playback_speed(value)
        if abs(clamped - value) > 1e-9:
            self.speed_spinbox.blockSignals(True)
            self.speed_spinbox.setValue(clamped)
            self.speed_spinbox.blockSignals(False)
        self.speed_spinbox.setSpecialValueText(format_playback_speed(MIN_PLAYBACK_SPEED))
        self.playbackSpeedChanged.emit(clamped)

    def playback_speed(self) -> float:
        return clamp_playback_speed(float(self.speed_spinbox.value()))

    def set_playback_speed(self, speed: float, *, emit_signals: bool = True) -> None:
        clamped = clamp_playback_speed(speed)
        if abs(clamped - self.playback_speed()) <= 1e-9:
            return

        self.speed_spinbox.blockSignals(True)
        self.speed_spinbox.setValue(clamped)
        self.speed_spinbox.blockSignals(False)
        if emit_signals:
            self.playbackSpeedChanged.emit(clamped)

    def step_playback_speed(self, delta_steps: int) -> None:
        next_speed = step_playback_speed(self.playback_speed(), delta_steps, floor=0.1)
        self.set_playback_speed(next_speed)

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
        self._total_label.setText(f"/ {total - 1}")

    def set_current_frame(self, frame: int) -> None:
        if self._user_is_scrubbing:
            return

        self.timeline_slider.blockSignals(True)
        self.frame_spinbox.blockSignals(True)
        self.timeline_slider.setValue(frame)
        self.frame_spinbox.setValue(frame)
        self.timeline_slider.blockSignals(False)
        self.frame_spinbox.blockSignals(False)
