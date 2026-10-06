"""Reusable QMainWindow base that applies ax-devil window chrome consistently."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import QMainWindow, QMenuBar, QWidget

from ax_devil.modules.chrome.title_bar import TitleBar
from ax_devil.modules.chrome.window_frame_controller import WindowFrameController
from ax_devil.modules.chrome.window_geometry import bounded_window_size, fit_window
from ax_devil.modules.settings.window_state import window_state_settings


def window_uses_custom_frame(window: QWidget) -> bool:
    """Return whether an app-owned window is using custom chrome."""
    return bool(getattr(window, "uses_custom_frame", False))


class ChromeWindow(QMainWindow):
    """QMainWindow base that optionally enables custom frameless chrome."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        use_custom_frame: bool = False,
        show_custom_frame_border: bool = False,
        remember_size: bool = False,
    ) -> None:
        super().__init__(parent)
        self._remember_size = remember_size
        self._positioned = False
        self._needs_default_size = False
        self._restored_size: QSize | None = None
        self.setMinimumSize(480, 320)
        self._use_custom_frame = use_custom_frame
        self._show_custom_frame_border = show_custom_frame_border
        self._title_bar: TitleBar | None = None
        self._frame_controller: WindowFrameController | None = None
        self._configure_window_flags()
        if self._use_custom_frame:
            self._title_bar = TitleBar(self)
            self.setMenuWidget(self._title_bar)
            self._frame_controller = WindowFrameController(self, show_border=self._show_custom_frame_border)
            self._frame_controller.initialize()

    def setVisible(self, visible: bool) -> None:
        """Prepare geometry before the desktop window manager first maps the window."""
        if visible and not self._positioned:
            self._positioned = True
            self._prepare_geometry()
        super().setVisible(visible)

    def _prepare_geometry(self) -> None:
        """Size the window before it maps; a window with a parent opens centered over it, a top-level one is placed
        by the desktop."""
        self.ensurePolished()
        if self._remember_size:
            settings = window_state_settings()
            size = cast(QSize, settings.value(f"{self.objectName()}/size", QSize(), type=QSize))
            if size.isValid():
                fit_window(self, size, center=True, parent_size_fraction=None)
                self._restored_size = size
                if settings.value(f"{self.objectName()}/maximized", False, type=bool):
                    self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
                return
            if self.parentWidget() is None:
                self._needs_default_size = True
                return
        fit_window(self, self.sizeHint(), center=True)

    def _apply_default_size(self) -> None:
        if self._restored_size is not None:
            restored_size = self._restored_size
            self._restored_size = None
            if not (self.isMaximized() or self.isFullScreen()):
                self.resize(bounded_window_size(self, restored_size))
        if not self._needs_default_size:
            return
        self._needs_default_size = False
        screen = self.screen()
        if screen is not None and not (self.isMaximized() or self.isFullScreen()):
            available = screen.availableGeometry().size()
            preferred = QSize(round(available.width() * 0.75), round(available.height() * 0.75))
            self.resize(bounded_window_size(self, preferred))

    def closeEvent(self, event: QCloseEvent) -> None:
        """Save size and maximized state without persisting desktop placement."""
        super().closeEvent(event)
        if event.isAccepted() and self._remember_size and self._positioned and not self._needs_default_size:
            settings = window_state_settings()
            settings.remove(self.objectName())
            settings.setValue(f"{self.objectName()}/size", self.normalGeometry().size())
            settings.setValue(f"{self.objectName()}/maximized", self.isMaximized())
            settings.sync()

    def menu_host(self) -> QMenuBar:
        """Return the menu bar host for this window (custom title bar or native menu bar)."""
        return self._title_bar.menu_bar if self._title_bar else self.menuBar()

    @property
    def uses_custom_frame(self) -> bool:
        """Return whether this window is using the shared custom frame."""
        return self._use_custom_frame

    @property
    def shows_custom_frame_border(self) -> bool:
        """Return whether this custom-framed window draws the shared inside border."""
        return self._use_custom_frame and self._show_custom_frame_border

    def _configure_window_flags(self) -> None:
        flags = Qt.WindowType.Window
        if self._use_custom_frame:
            flags |= Qt.WindowType.FramelessWindowHint
        self.setWindowFlags(flags)

    def changeEvent(self, event: QEvent) -> None:  # noqa: D401 - Qt override
        """Propagate window-state changes to custom chrome."""
        super().changeEvent(event)
        if event is None:
            return
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._apply_default_size()
        elif event.type() == QEvent.Type.WindowStateChange:
            if self._title_bar is not None:
                self._title_bar.sync_buttons()
            if self._frame_controller is not None:
                self._frame_controller.handle_window_state_change()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: D401 - Qt override
        """Keep frameless resize grips aligned with the window geometry."""
        super().resizeEvent(event)
        if self._frame_controller is not None:
            self._frame_controller.handle_resize_event(event)

    def restore_from_maximized(self, x_ratio: float, cursor_pos: QPoint, title_bar_height: int) -> None:
        """Restore from maximized and reposition the window under the cursor."""
        if self._frame_controller is not None:
            self._frame_controller.restore_from_maximized(x_ratio, cursor_pos, title_bar_height)
