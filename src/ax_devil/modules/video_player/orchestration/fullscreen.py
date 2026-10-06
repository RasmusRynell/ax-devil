"""Temporarily present an existing lane display in a fullscreen window."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QScreen, QShortcut
from PySide6.QtWidgets import QApplication, QLayout, QWidget

from ax_devil.modules.chrome.chrome_window import ChromeWindow

from ..ui.frame_display import FrameDisplay


class _FullscreenWindow(ChromeWindow):
    exitRequested = Signal()

    def __init__(self, parent: QWidget, screen: QScreen) -> None:
        super().__init__(parent)
        self._target_screen = screen
        # Position before showFullScreen chooses a native screen, and again at
        # first-show preparation instead of ChromeWindow's normal window fitting.
        self._prepare_geometry()

    def _prepare_geometry(self) -> None:
        self.setScreen(self._target_screen)
        self.setGeometry(self._target_screen.geometry())

    def closeEvent(self, event: QCloseEvent) -> None:
        """Treat closing the fullscreen window as returning to the workspace."""
        event.ignore()
        self.exitRequested.emit()


class LaneFullscreenController(QObject):
    """Move the focused display into fullscreen without changing its playback owner."""

    def __init__(self, window: QWidget, actions: Sequence[QAction]) -> None:
        super().__init__(window)
        self._window = window
        self._actions = actions
        self._host: _FullscreenWindow | None = None
        self._display: FrameDisplay | None = None
        self._placeholder: QWidget | None = None
        self._layout: QLayout | None = None

    def toggle(self) -> None:
        """Toggle fullscreen for the display containing keyboard focus."""
        if self._host is not None:
            self.exit()
            return
        widget = QApplication.focusWidget()
        while widget is not None and not isinstance(widget, FrameDisplay):
            widget = widget.parentWidget()
        if widget is None or widget.window() is not self._window:
            return
        screen = QApplication.screenAt(widget.mapToGlobal(widget.rect().center())) or widget.screen()
        if screen is None:
            return
        parent = widget.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is None:
            return
        placeholder = QWidget(parent)
        placeholder.setSizePolicy(widget.sizePolicy())
        item = layout.replaceWidget(widget, placeholder)
        if item is None:
            placeholder.deleteLater()
            return
        del item
        self._display = widget
        self._layout = layout
        self._placeholder = placeholder
        host = _FullscreenWindow(self._window, screen)
        self._host = host
        host.addActions(self._actions)
        host.setCentralWidget(widget)
        host.exitRequested.connect(self.exit)
        escape = QShortcut(QKeySequence("Esc"), host)
        escape.activated.connect(self.exit)
        widget.aboutToCleanup.connect(self.exit)
        host.showFullScreen()
        host.activateWindow()
        widget.viewport.setFocus()

    def exit(self) -> None:
        """Restore the display before its owner changes entries or tears it down."""
        host = self._host
        display = self._display
        placeholder = self._placeholder
        layout = self._layout
        if host is None or display is None or placeholder is None or layout is None:
            return
        self._host = None
        self._display = None
        self._placeholder = None
        self._layout = None
        display.aboutToCleanup.disconnect(self.exit)
        host.takeCentralWidget()
        display.setParent(placeholder.parentWidget())
        item = layout.replaceWidget(placeholder, display)
        del item
        placeholder.deleteLater()
        display.show()
        host.hide()
        host.deleteLater()
        self._window.activateWindow()
        display.viewport.setFocus()
