"""Restart ax-devil with the command line it was started with."""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, QProcess
from PySide6.QtWidgets import QApplication

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_RESTART_PROPERTY = "axDevilRestartRequested"


def restart_application() -> None:
    """Close every window and quit; the app starts again once it has saved its state on exit.

    The new process is started by `relaunch_if_requested`, which the app calls after its exit-time saves, so the
    restarted session reads what this one saved.
    """
    app = QApplication.instance()
    if app is None:
        return
    app.setProperty(_RESTART_PROPERTY, True)
    QApplication.closeAllWindows()
    QApplication.quit()


def relaunch_if_requested(app: QCoreApplication) -> None:
    """Start ax-devil again with this process's command line when a restart was requested; call last on exit."""
    if not app.property(_RESTART_PROPERTY):
        return
    app.setProperty(_RESTART_PROPERTY, False)
    program, *arguments = sys.orig_argv
    started, _pid = QProcess.startDetached(program, arguments)
    if not started:
        logger.error(f"Could not restart ax-devil with {program}")
