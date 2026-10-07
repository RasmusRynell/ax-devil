"""Draggable Components for Video Player

Simple draggable handle and collapsible panel system integrated with video player.
Allows adding any widget to a side panel that can be expanded/collapsed via dragging.
"""

from typing import Optional, Protocol

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.settings.logging_config import get_logger

from ..constants import (
    DEFAULT_AUTO_HIDE_DELAY,
    DEFAULT_FADE_DURATION,
    DRAG_THRESHOLD_PIXELS,
    HANDLE_HEIGHT,
    HANDLE_ICON_SIZE_PX,
    HANDLE_WIDTH,
    PANEL_ANIMATION_DURATION,
    PANEL_COLLAPSED_WIDTH,
    PANEL_EXPANDED_WIDTH,
)
from .fading import FadingWidget

logger = get_logger(__name__)


class DraggableTarget(Protocol):
    """Protocol defining the interface for objects that can be dragged/resized."""

    is_collapsed: bool

    @property
    def expanded_width(self) -> int: ...

    def width(self) -> int: ...
    def setFixedWidth(self, width: int, /) -> None: ...
    def expand(self) -> None: ...
    def collapse(self) -> None: ...


class DraggableHandle(FadingWidget):
    """A draggable handle widget for resizing panels."""

    dragStarted = Signal(float)
    dragMoved = Signal(float)
    dragEnded = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent=parent,
            fade_duration=DEFAULT_FADE_DURATION,
            auto_hide_delay=DEFAULT_AUTO_HIDE_DELAY,
            initial_opacity=0.0,
            hover_element_name="drag_handle",
        )

        self._logger = logger
        self.is_dragging = False
        self.drag_start_x = 0.0
        self.drag_start_width = 0
        self.target_object: Optional[DraggableTarget] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the handle UI with draggable dots."""
        self.setFixedSize(HANDLE_WIDTH, HANDLE_HEIGHT)
        self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        grip = QLabel(self)
        grip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(grip)
        size = QSize(HANDLE_ICON_SIZE_PX, HANDLE_ICON_SIZE_PX)
        follow_appearance(grip, lambda: grip.setPixmap(Icon.DRAG.icon().pixmap(size, grip.devicePixelRatioF())))

    def setTarget(self, target: DraggableTarget) -> None:
        """Set the target object to be controlled by this handle."""
        self.target_object = target
        self._logger.debug(f"Target object set to: {type(target).__name__}")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press to start dragging."""
        if event.button() == Qt.MouseButton.LeftButton and self.target_object:
            # Don't immediately start dragging - wait for mouseMoveEvent
            # This allows double-click to work properly
            self.drag_start_x = float(event.globalPosition().x())
            self.drag_start_width = self.target_object.width()
            self._logger.debug(f"Mouse press at position: {self.drag_start_x:f}")
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move during dragging."""
        if self.target_object:
            x = float(event.globalPosition().x())

            # Start dragging only when mouse actually moves
            if not self.is_dragging and abs(x - self.drag_start_x) > DRAG_THRESHOLD_PIXELS:
                self.is_dragging = True
                self.dragStarted.emit(self.drag_start_x)
                self._logger.debug(f"Drag started at position: {self.drag_start_x:f}")

            # Continue dragging if already started
            if self.is_dragging:
                delta_x = x - self.drag_start_x
                new_width = max(0, min(self.target_object.expanded_width, self.drag_start_width - delta_x))
                self.target_object.setFixedWidth(int(new_width))
                self.dragMoved.emit(x)
                self._logger.debug(f"Dragging to position: {x:f}, width: {int(new_width)}")
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release to end dragging."""
        if self.is_dragging and self.target_object:
            self.is_dragging = False
            # Snap to expanded or collapsed based on current width
            action = (
                self.target_object.collapse
                if self.target_object.width() < self.target_object.expanded_width * 0.5
                else self.target_object.expand
            )
            action()
            x = float(event.globalPosition().x())
            self.dragEnded.emit(x)
            self._logger.debug(f"Drag ended at position: {x:f}")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Handle double-click to toggle panel expand/collapse instantly."""
        if event.button() == Qt.MouseButton.LeftButton and self.target_object:
            # Reset any drag state that might have been triggered
            self.is_dragging = False
            # Toggle instantly without animation
            if self.target_object.is_collapsed:
                self.target_object.expand()
            else:
                self.target_object.collapse()
            self._logger.debug("Double-click instant toggle panel")
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class DraggablePanel(FadingWidget):
    """
    Base class for panels that can be controlled by DraggableHandle.
    Provides expand/collapse animations and widget content management.
    """

    # Signals for panel state changes
    panelToggled = Signal(bool)  # True when expanded, False when collapsed

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        animation_duration: int = PANEL_ANIMATION_DURATION,
        collapsed_width: int = PANEL_COLLAPSED_WIDTH,
        expanded_width: int = PANEL_EXPANDED_WIDTH,
    ) -> None:
        # Set panel configuration BEFORE calling super().__init__
        self.animation_duration: int = animation_duration
        self.collapsed_width: int = collapsed_width
        self._expanded_width = expanded_width
        self.is_collapsed: bool = True

        # Animation components (will be initialized in _setup_animations)
        self.animation: QPropertyAnimation
        self.min_animation: QPropertyAnimation

        # Content widget management
        self._content_widget: Optional[QWidget] = None
        self._content_layout: QVBoxLayout

        super().__init__(
            parent=parent,
            fade_duration=DEFAULT_FADE_DURATION,
            auto_hide_delay=DEFAULT_AUTO_HIDE_DELAY,
            initial_opacity=1.0,  # Panel is always visible when shown
            hover_element_name="side_panel",
        )

        self._logger = logger

        self._setup_animations()
        self._setup_ui()

        # Start collapsed
        self.setFixedWidth(self.collapsed_width)

    @property
    def expanded_width(self) -> int:
        """Return the open width: the configured width, widened when the content needs more, as at large text."""
        return max(self._expanded_width, self._content_minimum_width())

    def _content_minimum_width(self) -> int:
        content = self._content_widget
        if content is None:
            return 0
        margins = self._content_layout.contentsMargins()
        return content.minimumSizeHint().width() + margins.left() + margins.right()

    def event(self, event: QEvent) -> bool:
        """Widen an open panel whose content grew, as after a live text-size change, instead of clipping it."""
        if (
            event.type() == QEvent.Type.LayoutRequest
            and not self.is_collapsed
            and self.animation.state() != QAbstractAnimation.State.Running
            and self.width() < self._content_minimum_width()
        ):
            self.setFixedWidth(self._content_minimum_width())
        return super().event(event)

    def _setup_animations(self) -> None:
        """Setup smooth animations for expand/collapse."""
        # First call parent to setup fade animations and opacity effect
        super()._setup_animations()

        # Then setup panel-specific animations
        self.animation = QPropertyAnimation(self, b"maximumWidth")
        self.animation.setDuration(self.animation_duration)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.min_animation = QPropertyAnimation(self, b"minimumWidth")
        self.min_animation.setDuration(self.animation_duration)
        self.min_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self._hide_content_when_collapsed)

    def _setup_ui(self) -> None:
        """Setup the panel UI with content area."""
        # Background and borders will be handled by paintEvent
        self.setAutoFillBackground(True)

        # Main content layout
        self._content_layout = QVBoxLayout(self)
        self._content_layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        self._content_layout.setSpacing(Space.S)

    def set_content_widget(self, widget: Optional[QWidget]) -> None:
        """Set the widget to display in the panel content area."""
        self._clear_content_widget()

        content_widget = widget
        if content_widget is None:
            content_widget = QLabel("No content")
            content_widget.setStyleSheet("color: palette(placeholder-text); font-style: italic;")
            content_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._content_widget = content_widget
        self._content_layout.addWidget(content_widget)
        # Hidden content skips the work of tools that follow playback while nobody can see them.
        content_widget.setVisible(not self.is_collapsed)

        if widget is not None:
            self._logger.debug(f"Set content widget: {widget.__class__.__name__}")

    def collapse(self) -> None:
        """Collapse the panel."""
        if self.is_collapsed and self.width() == self.collapsed_width:
            return

        self.is_collapsed = True

        self.animation.setStartValue(self.width())
        self.animation.setEndValue(self.collapsed_width)
        self.animation.start()

        self.min_animation.setStartValue(self.minimumWidth())
        self.min_animation.setEndValue(self.collapsed_width)
        self.min_animation.start()

        self.panelToggled.emit(False)
        self._logger.debug("Panel collapsed")

    def expand(self) -> None:
        """Expand the panel."""
        if not self.is_collapsed and self.width() == self.expanded_width:
            return

        self.is_collapsed = False
        if self._content_widget is not None:
            self._content_widget.show()

        self.animation.setStartValue(self.width())
        self.animation.setEndValue(self.expanded_width)
        self.animation.start()

        self.min_animation.setStartValue(self.minimumWidth())
        self.min_animation.setEndValue(self.expanded_width)
        self.min_animation.start()

        self.panelToggled.emit(True)
        self._logger.debug("Panel expanded")

    def _hide_content_when_collapsed(self) -> None:
        """Hide the content once a collapse finishes; a zero-width panel still counts as visible to Qt."""
        if self.is_collapsed and self._content_widget is not None:
            self._content_widget.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Custom paint to create themed panel with accent border."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()

        # Get theme colors
        bg_color = self.palette().color(QPalette.ColorRole.Window)
        highlight_color = self.palette().color(QPalette.ColorRole.Highlight)
        border_color = self.palette().color(QPalette.ColorRole.Mid)

        # Draw background
        painter.setBrush(bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(rect)

        # Draw accent border on the left
        painter.setBrush(highlight_color)
        painter.drawRect(0, 0, 3, rect.height())

        # Draw subtle borders on other sides
        painter.setPen(border_color)
        painter.drawLine(rect.topRight(), rect.bottomRight())  # right
        painter.drawLine(rect.topLeft(), rect.topRight())  # top
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())  # bottom

        super().paintEvent(event)

    def cleanup(self) -> None:
        """Clean up animation resources and current content."""
        super().cleanup()
        self.animation.stop()
        self.min_animation.stop()
        self._clear_content_widget(delete_widget=True)

        self._logger.debug("DraggablePanel cleanup completed")

    def _clear_content_widget(self, *, delete_widget: bool = False) -> None:
        if self._content_widget is None:
            return

        self._content_layout.removeWidget(self._content_widget)
        self._content_widget.setParent(None)
        if delete_widget:
            self._content_widget.deleteLater()
        self._content_widget = None

    def __del__(self) -> None:
        """Destructor to ensure cleanup."""
        try:
            self.cleanup()
        except Exception:
            # Ignore cleanup errors during destruction
            pass
