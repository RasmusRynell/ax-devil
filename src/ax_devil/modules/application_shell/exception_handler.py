"""Log uncaught Python exceptions and offer to continue or close the Qt application."""

from __future__ import annotations

import sys
import traceback
from types import TracebackType

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class ExceptionHandler(QObject):
    """Global exception handler for PySide6 applications."""

    exception_occurred = Signal(Exception, str)

    def __init__(self) -> None:
        """Dispatch exception dialogs to the GUI thread."""
        super().__init__()
        self._exception_count = 0
        self._max_exceptions = 10  # Prevent infinite error loops

        self.exception_occurred.connect(self._handle_exception_in_main_thread)

    def _exception_hook(
        self, exc_type: type[BaseException], exc_value: BaseException, exc_traceback: TracebackType | None
    ) -> None:
        """Handle uncaught exceptions."""
        if self._exception_count >= self._max_exceptions:
            logger.critical("Too many exceptions occurred, terminating application")
            sys.exit(1)

        self._exception_count += 1

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

        logger.error(f"Uncaught exception: {exc_type.__name__}: {exc_value}")
        logger.error(f"Stack trace:\n{tb_text}")

        self.exception_occurred.emit(exc_value, tb_text)

    def _handle_exception_in_main_thread(self, exception: Exception, traceback_text: str) -> None:
        """Handle exception in the main thread (safe for GUI operations)."""
        self._show_error_dialog(exception, traceback_text)
        QTimer.singleShot(5000, self._reset_exception_count)

    def _reset_exception_count(self) -> None:
        """Reset the exception count (recovery mechanism)."""
        if self._exception_count > 0:
            self._exception_count -= 1
            logger.debug(f"Exception count reset to: {self._exception_count}")

    def _show_error_dialog(self, exception: Exception, traceback_text: str) -> None:
        """Show a user-friendly error dialog."""
        try:
            app = QApplication.instance()
            if not app:
                return

            dialog = QMessageBox()
            dialog.setIcon(QMessageBox.Icon.Critical)
            dialog.setWindowTitle("Application Error")

            dialog.setText(f"An unexpected error occurred:\n\n{type(exception).__name__}: {exception}")

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

            dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Close)

            dialog.setDefaultButton(QMessageBox.StandardButton.Ok)

            result = dialog.exec()

            if result == QMessageBox.StandardButton.Close:
                logger.info("User chose to close application after error")
                app.quit()
            else:
                logger.info("User chose to continue after error")

        except Exception as dialog_error:
            logger.error(f"Failed to show error dialog: {dialog_error}")


def install_exception_handler() -> None:
    """Install GUI exception reporting; the bound hook retains its handler."""
    handler = ExceptionHandler()
    sys.excepthook = handler._exception_hook
    logger.debug("Global exception handler installed")
