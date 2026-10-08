"""Restart ax-devil with the command line it was started with."""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import QApplication

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


def restart_application() -> None:
    """Close every window, quit, and start ax-devil again once the windows have saved their state."""
    app = QApplication.instance()
    if app is None:
        return
    program, *arguments = sys.orig_argv
    app.aboutToQuit.connect(lambda: _start(program, arguments), Qt.ConnectionType.SingleShotConnection)
    QApplication.closeAllWindows()
    QApplication.quit()


def _start(program: str, arguments: list[str]) -> None:
    if not QProcess.startDetached(program, arguments):
        logger.error(f"Could not restart ax-devil with {program}")
