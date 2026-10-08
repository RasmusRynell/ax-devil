"""Fading Widget Base Class

Generic base class that adds fade in/out functionality to any QWidget.
Provides smooth opacity animations and auto-hide functionality for video overlays.
"""

from typing import Optional, cast

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QTimer
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QGraphicsEffect, QGraphicsOpacityEffect, QWidget

from ax_devil.modules.settings.logging_config import get_logger

from ..constants import DEFAULT_AUTO_HIDE_DELAY, DEFAULT_FADE_DURATION
from ..orchestration.interaction_types import HoverSink

logger = get_logger(__name__)


class FadingWidget(QWidget):
    """
    Base class that adds fade in/out functionality to any QWidget.

    Provides smooth opacity animations and auto-hide functionality
    that widgets can inherit from directly.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        fade_duration: int = DEFAULT_FADE_DURATION,
        auto_hide_delay: int = DEFAULT_AUTO_HIDE_DELAY,
        initial_opacity: float = 0.0,
        hover_element_name: Optional[str] = None,
    ) -> None:
        super().__init__(parent)

        self._logger = logger

        # Fade configuration
        self.fade_duration = fade_duration
        self.auto_hide_delay = auto_hide_delay

        # Store hover element name for event notifications
        self.hover_element_name = hover_element_name or self.__class__.__name__.lower()
        self._hover_sink: HoverSink | None = None

        # Animation components
        self.opacity_effect: QGraphicsOpacityEffect
        self.fade_animation: QPropertyAnimation
        self.hide_timer: QTimer
        self._cleaned_up = False

        self._setup_animations()
        self._setup_auto_hide(initial_opacity)

    def _setup_animations(self) -> None:
        """Setup opacity fade animations."""
        # Opacity effect
        self.opacity_effect = QGraphicsOpacityEffect()
        self.setGraphicsEffect(self.opacity_effect)

        # Fade animation
        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_animation.setDuration(self.fade_duration)
        self.fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _setup_auto_hide(self, initial_opacity: float) -> None:
        """Setup auto-hide timer."""
        self.hide_timer = QTimer()
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.fade_out)

        # Set initial opacity
        self.opacity_effect.setOpacity(initial_opacity)

    def fade_in(self) -> None:
        """Fade widget in."""
        if self._cleaned_up:
            return
        current_opacity = self.opacity_effect.opacity()

        # Skip if already fully visible
        if current_opacity >= 1.0:
            self.hide_timer.stop()
            return

        # Skip if already animating to fade in
        if self.fade_animation.state() == QPropertyAnimation.State.Running and self.fade_animation.endValue() == 1.0:
            self.hide_timer.stop()
            return

        self.hide_timer.stop()
        self.fade_animation.setStartValue(current_opacity)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.start()
        self._logger.debug(f"Fading in {self.hover_element_name}")

    def fade_out(self) -> None:
        """Fade widget out."""
        if self._cleaned_up:
            return
        current_opacity = self.opacity_effect.opacity()

        # Skip if already fully invisible
        if current_opacity <= 0.0:
            return

        # Skip if already animating to fade out
        if self.fade_animation.state() == QPropertyAnimation.State.Running and self.fade_animation.endValue() == 0.0:
            return

        self.fade_animation.setStartValue(current_opacity)
        self.fade_animation.setEndValue(0.0)
        self.fade_animation.start()
        self._logger.debug(f"Fading out {self.hover_element_name}")

    def start_auto_hide_timer(self) -> None:
        """Start the auto-hide timer; a cleaned-up widget no longer fades."""
        if self._cleaned_up:
            return
        self.hide_timer.start(self.auto_hide_delay)
        self._logger.debug(f"Started auto-hide timer for {self.hover_element_name}")

    def set_hover_sink(self, hover_sink: HoverSink) -> None:
        """Register the target that receives hover enter/leave notifications."""
        self._hover_sink = hover_sink

    def enterEvent(self, event: QEnterEvent) -> None:
        """Mouse entered the widget."""
        if self._hover_sink is not None:
            self._hover_sink.on_hover_enter(self.hover_element_name)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        """Mouse left the widget."""
        if self._hover_sink is not None:
            self._hover_sink.on_hover_leave(self.hover_element_name)
        super().leaveEvent(event)

    def cleanup(self) -> None:
        """Clean up animation resources to prevent segfaults."""
        if self._cleaned_up:
            return

        self._cleaned_up = True
        # Stop animations before the widget tears down.
        try:
            self.fade_animation.stop()
        except RuntimeError:
            pass
        try:
            self.hide_timer.stop()
        except RuntimeError:
            pass

        effect = self.opacity_effect
        try:
            self.setGraphicsEffect(cast(QGraphicsEffect, None))
        except RuntimeError:
            pass
        try:
            effect.deleteLater()
        except RuntimeError:
            pass

    def __del__(self) -> None:
        """Destructor to ensure cleanup."""
        try:
            self.cleanup()
        except Exception:
            logger.debug(f"Fading widget id={id(self)} failed to clean up on garbage collection", exc_info=True)
