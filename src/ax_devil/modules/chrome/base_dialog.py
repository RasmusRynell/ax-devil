"""Base dialog class for all secondary windows for ax-devil.

This module provides the BaseDialog class that handles common dialog functionality like theming, window management, and
consistent styling across all dialogs.
"""

from collections.abc import Callable
from types import TracebackType
from typing import TypeVar

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QCloseEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.chrome_window import window_uses_custom_frame
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea
from ax_devil.modules.chrome.title_bar import TitleBar
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.chrome.window_frame_controller import WindowFrameController
from ax_devil.modules.chrome.window_geometry import fit_window
from ax_devil.modules.settings.logging_config import get_logger

_DialogT = TypeVar("_DialogT", bound="BaseDialog")


class BaseDialog(QDialog):
    """Base class for all dialog windows.

    Provides common functionality for dialog windows including:
    - Consistent theming and styling
    - Standard window properties and behavior
    - Common button layouts and handling
    - Proper parent-child relationships
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        title: str = "Dialog",
        modal: bool = True,
        *,
        scroll_content: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 200)

        self._logger = get_logger(__name__)
        self._use_custom_frame = self._resolve_use_custom_frame(parent)
        self._title_bar: TitleBar | None = None
        self._frame_controller: WindowFrameController | None = None

        self._positioned = False
        self._setup_window_properties(title, modal)
        self._setup_base_layout(scroll_content=scroll_content)
        if self._use_custom_frame:
            self._frame_controller = WindowFrameController(self, show_border=True)
            self._frame_controller.initialize()

        self._logger.debug(f"BaseDialog '{title}' initialized")

    def __enter__(self: _DialogT) -> _DialogT:
        """Keep a temporary dialog alive until its caller has consumed the result."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release resources and schedule deletion when a temporary dialog's scope ends."""
        self.cleanup()
        self.deleteLater()

    def cleanup(self) -> None:
        """Release dialog-owned resources; resource-owning subclasses override this idempotent hook."""

    def done(self, result: int) -> None:
        """Clean up on acceptance, rejection, Escape, and window close."""
        self.cleanup()
        super().done(result)

    def _resolve_use_custom_frame(self, parent: QWidget | None) -> bool:
        """Inherit custom-frame usage from the top-level parent window when available."""
        if parent is None:
            return False
        return window_uses_custom_frame(parent.window())

    @property
    def uses_custom_frame(self) -> bool:
        """Expose the frame policy to dialogs opened from this dialog."""
        return self._use_custom_frame

    @property
    def shows_custom_frame_border(self) -> bool:
        """Return whether this dialog draws the shared custom-frame border."""
        return self._use_custom_frame

    def _setup_window_properties(self, title: str, modal: bool) -> None:
        """Configure basic window properties."""
        self.setWindowTitle(title)
        self.setModal(modal)
        if self._use_custom_frame:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)

    def _setup_base_layout(self, *, scroll_content: bool) -> None:
        """Set up the base layout structure."""
        layout_parent: QWidget = self
        if self._use_custom_frame:
            outer_layout = QVBoxLayout(self)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            outer_layout.setSpacing(0)

            self._title_bar = TitleBar(
                self,
                show_menu_bar=False,
                show_minimize=False,
                show_maximize=False,
            )
            outer_layout.addWidget(self._title_bar)

            content_host = QWidget(self)
            outer_layout.addWidget(content_host)
            layout_parent = content_host

        self._main_layout = QVBoxLayout(layout_parent)
        self._main_layout.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        self._main_layout.setSpacing(Space.L)

        # Only content scrolls; actions remain visible at every window size.
        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(Space.L)
        if scroll_content:
            self._scroll_area = ContentScrollArea(self._content_widget)
            self._main_layout.addWidget(self._scroll_area, 1)
        else:
            self._main_layout.addWidget(self._content_widget, 1)

        # Button area
        self._button_layout = QHBoxLayout()
        self._button_layout.addStretch()  # Push buttons to the right
        self._main_layout.addLayout(self._button_layout)

    def setVisible(self, visible: bool) -> None:
        """Prepare initial geometry before the desktop maps the dialog."""
        if visible and not self._positioned:
            self._positioned = True
            self.ensurePolished()
            metrics = self.fontMetrics()
            fit_window(
                self,
                self.opening_size_hint(),
                center=True,
                parent_size_fraction=None,
                maximum_size=QSize(120 * metrics.averageCharWidth(), 48 * metrics.lineSpacing()),
            )
        super().setVisible(visible)

    def opening_size_hint(self) -> QSize:
        """Return the size to open at; a dialog whose own controls change its content's size returns the largest."""
        return self.sizeHint()

    def showEvent(self, event: QShowEvent) -> None:
        """Refresh custom chrome when the dialog is shown."""
        super().showEvent(event)
        if self._title_bar is not None:
            self._title_bar.sync_buttons()

    def changeEvent(self, event: QEvent) -> None:  # noqa: D401 - Qt override
        """Propagate window-state changes to custom chrome."""
        super().changeEvent(event)
        if event is None:
            return
        if event.type() == QEvent.Type.WindowStateChange:
            if self._title_bar is not None:
                self._title_bar.sync_buttons()
            if self._frame_controller is not None:
                self._frame_controller.handle_window_state_change()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: D401 - Qt override
        """Keep frameless resize grips aligned with the dialog geometry."""
        super().resizeEvent(event)
        if self._frame_controller is not None:
            self._frame_controller.handle_resize_event(event)

    def add_content_widget(self, widget: QWidget, *, stretch: int = 0) -> None:
        """Add a widget to the content area.

        Args:
            widget: Widget to add to the dialog content
            stretch: Share of additional space given to a view or editor
        """
        self._content_layout.addWidget(widget, stretch)

    def add_button(
        self, text: str, callback: Callable[[], None] | None = None, is_default: bool = False
    ) -> QPushButton:
        """Add a button to the dialog button area.

        Args:
            text: Button text
            callback: Function to call when button is clicked
            is_default: Whether this is the default button

        Returns:
            The created button
        """
        button = QPushButton(text)

        if callback:
            button.clicked.connect(callback)

        if is_default:
            button.setDefault(True)
            button.setAutoDefault(True)

        self._button_layout.addWidget(button)
        return button

    def add_standard_buttons(self, include_cancel: bool = True) -> tuple[QPushButton, QPushButton | None]:
        """Add standard OK/Cancel buttons.

        Args:
            include_cancel: Whether to include a Cancel button

        Returns:
            Tuple of (OK button, Cancel button or None)
        """
        ok_button = self.add_button("OK", self.accept, is_default=True)

        cancel_button = None
        if include_cancel:
            cancel_button = self.add_button("Cancel", self.reject)

        return ok_button, cancel_button

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle dialog close event."""
        self._logger.debug(f"Dialog '{self.windowTitle()}' closing")
        super().closeEvent(event)

    def restore_from_maximized(self, x_ratio: float, cursor_pos: QPoint, title_bar_height: int) -> None:
        """Restore from maximized and reposition the dialog under the cursor."""
        if self._frame_controller is not None:
            self._frame_controller.restore_from_maximized(x_ratio, cursor_pos, title_bar_height)
