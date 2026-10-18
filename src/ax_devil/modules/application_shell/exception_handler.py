"""Global exception handler for ax-devil.

This module provides comprehensive exception handling for PySide6 applications:
- Catches all uncaught exceptions
- Logs exceptions with full stack traces
- Shows user-friendly error dialogs
- Prevents application crashes
- Provides recovery options
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from types import TracebackType
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from ax_devil.modules.settings.logging_config import get_logger

ExceptHook = Callable[[type[BaseException], BaseException, TracebackType | None], Any]


class ExceptionHandler(QObject):
    """Global exception handler for PySide6 applications."""

    # Signal emitted when an exception occurs
    exception_occurred = Signal(Exception, str)

    def __init__(self, show_dialog: bool = True) -> None:
        super().__init__()
        self._logger = get_logger(__name__)
        self._show_dialog = show_dialog
        self._exception_count = 0
        self._max_exceptions = 10  # Prevent infinite error loops
        self._original_excepthook: ExceptHook | None = None

        # Connect to our own signal to handle exceptions in the main thread
        self.exception_occurred.connect(self._handle_exception_in_main_thread)

    def install(self) -> None:
        """Install the global exception handler."""
        self._original_excepthook = sys.excepthook
        sys.excepthook = self._exception_hook
        self._logger.debug("Global exception handler installed")

    def _exception_hook(
        self, exc_type: type[BaseException], exc_value: BaseException, exc_traceback: TracebackType | None
    ) -> None:
        """Handle uncaught exceptions."""
        # Prevent infinite loops
        if self._exception_count >= self._max_exceptions:
            self._logger.critical("Too many exceptions occurred, terminating application")
            sys.exit(1)

        self._exception_count += 1

        # Format the exception information
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
        tb_text = "".join(tb_lines)

        # Log the exception
        self._logger.error(f"Uncaught exception: {exc_type.__name__}: {exc_value}")
        self._logger.error(f"Stack trace:\n{tb_text}")

        # Emit signal to handle in main thread (for GUI operations)
        self.exception_occurred.emit(exc_value, tb_text)

    def _handle_exception_in_main_thread(self, exception: Exception, traceback_text: str) -> None:
        """Handle exception in the main thread (safe for GUI operations)."""
        if self._show_dialog:
            self._show_error_dialog(exception, traceback_text)

        # Reset exception count after a delay (recovery mechanism)
        QTimer.singleShot(5000, self._reset_exception_count)

    def _reset_exception_count(self) -> None:
        """Reset the exception count (recovery mechanism)."""
        if self._exception_count > 0:
            self._exception_count = max(0, self._exception_count - 1)
            self._logger.debug(f"Exception count reset to: {self._exception_count}")

    def _show_error_dialog(self, exception: Exception, traceback_text: str) -> None:
        """Show a user-friendly error dialog."""
        try:
            # Get the application instance
            app = QApplication.instance()
            if not app:
                return

            # Create error dialog
            dialog = QMessageBox()
            dialog.setIcon(QMessageBox.Icon.Critical)
            dialog.setWindowTitle("Application Error")

            # Main error message
            main_message = f"An unexpected error occurred:\n\n{type(exception).__name__}: {str(exception)}"
            dialog.setText(main_message)

            # Detailed information
            detailed_message = f"""
Technical Details:
{traceback_text}

What you can do:
• Try the action again
• Save your work if possible
• Restart the application if problems persist
• Report this error if it continues to occur

The application will attempt to continue running, but some features may not work properly.
            """.strip()

            dialog.setDetailedText(detailed_message)

            # Add buttons
            dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Close)

            # Set default button
            dialog.setDefaultButton(QMessageBox.StandardButton.Ok)

            # Show dialog
            result = dialog.exec()

            # Handle user choice
            if result == QMessageBox.StandardButton.Close:
                self._logger.info("User chose to close application after error")
                app.quit()
            else:
                self._logger.info("User chose to continue after error")

        except Exception as dialog_error:
            # If we can't show the dialog, at least log it
            self._logger.error(f"Failed to show error dialog: {dialog_error}")


# Global exception handler instance
_exception_handler: ExceptionHandler | None = None


def install_exception_handler(show_dialog: bool = True) -> ExceptionHandler:
    """Install the global exception handler."""
    global _exception_handler
    _exception_handler = ExceptionHandler(show_dialog=show_dialog)
    _exception_handler.install()
    return _exception_handler
