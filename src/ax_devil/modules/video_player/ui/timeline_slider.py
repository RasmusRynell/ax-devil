"""The timeline slider of the playback controls: a thin transparent track with click-to-jump and cached frames."""

from PySide6 import QtCore, QtGui, QtWidgets

from ..constants import (
    TIMELINE_CACHED_ALPHA,
    TIMELINE_GROOVE_HEIGHT,
    TIMELINE_GROOVE_HOVER_HEIGHT,
    TIMELINE_HANDLE_RADIUS,
    TIMELINE_PROGRESS_ALPHA_DISABLED,
    TIMELINE_PROGRESS_ALPHA_ENABLED,
    TIMELINE_TRACK_ALPHA_DISABLED,
    TIMELINE_TRACK_ALPHA_ENABLED,
)


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
        parent: QtWidgets.QWidget | None = None,
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
