"""The base of the playback controls: the scrim, the timeline slot and the row that drops controls when narrow."""

from PySide6 import QtCore, QtGui, QtWidgets

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole
from ax_devil.modules.settings.logging_config import get_logger

from ..constants import CONTROL_PANEL_PADDING, DEFAULT_AUTO_HIDE_DELAY_MS, DEFAULT_FADE_DURATION_MS
from .fading import FadingWidget
from .timeline_slider import TimelineSliderPainter

logger = get_logger(__name__)

_ICON_PX = 16
_SCRIM_ICON_STROKE = 2.0

# White controls on the dark video scrim. Fonts are set on the widgets, so they follow the text size.
BUTTON_STYLE = f"""
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


def video_control_icon(icon: Icon) -> QtGui.QIcon:
    """Return a white playback icon for controls on the dark video scrim."""
    return icon.icon(QtGui.QColor(QtCore.Qt.GlobalColor.white), stroke=_SCRIM_ICON_STROKE)


class BaseVideoControlPanel(FadingWidget):
    """Base video control panel with play/pause functionality and fading overlay support.

    The panel is a timeline over one compact row of controls on a dark scrim. When the row is too narrow, the
    widgets registered with ``_add_optional`` hide in that order until the rest fits.
    """

    playRequested = QtCore.Signal()
    pauseRequested = QtCore.Signal()
    stateChanged = QtCore.Signal(bool)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        # Initialize FadingWidget with control panel-specific settings
        super().__init__(
            parent=parent,
            fade_duration_ms=DEFAULT_FADE_DURATION_MS,
            auto_hide_delay_ms=DEFAULT_AUTO_HIDE_DELAY_MS,
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
            # The row and its own widgets only, so the speed menu that pops up from the row keeps the theme's surface.
            "#playbackControlsRow, #playbackControlsRow > QWidget { background-color: transparent; border: 0px; }"
            f"{BUTTON_STYLE}"
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
        label = "Pause" if self._playing else "Play"
        self.play_pause_btn.setToolTip(label)
        self.play_pause_btn.setAccessibleName(label)

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
