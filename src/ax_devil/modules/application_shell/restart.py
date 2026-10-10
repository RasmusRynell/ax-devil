"""Restart ax-devil with the command line it was started with, restoring the kept workspace."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QCoreApplication, QProcess
from PySide6.QtWidgets import QApplication, QWidget

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_RESTART_PROPERTY = "axDevilRestartRequested"
RESTARTED_ENVIRONMENT_VARIABLE = "AX_DEVIL_RESTARTED"
"""Marks a restarted process, which restores the kept workspace instead of reopening the command line's content."""


def restart_application(main_window: QWidget) -> None:
    """Close *main_window* and every other window, and quit; the app starts again once it has saved its state.

    The main window closes first, so its own cleanup (leaving lane fullscreen, stopping viewers and workers) runs even
    when another window refuses to close. The new process is started by `relaunch_if_requested`, which the app calls
    after its exit-time saves, so the restarted session reads what this one saved.
    """
    app = QApplication.instance()
    if app is None:
        return
    app.setProperty(_RESTART_PROPERTY, True)
    main_window.close()
    QApplication.closeAllWindows()
    QApplication.quit()


def relaunch_if_requested(app: QCoreApplication) -> None:
    """Start ax-devil again with this process's command line when a restart was requested; call last on exit.

    The new process is marked as a restart, so it keeps the command line's options but not its content.
    """
    if not app.property(_RESTART_PROPERTY):
        return
    app.setProperty(_RESTART_PROPERTY, False)
    program, *arguments = sys.orig_argv
    os.environ[RESTARTED_ENVIRONMENT_VARIABLE] = "1"
    started, _pid = QProcess.startDetached(program, arguments)
    if not started:
        logger.error(f"Could not restart ax-devil with {program}")


def is_restarted_launch() -> bool:
    """Return whether this process was started by a restart, and clear the mark so processes it starts are not."""
    return os.environ.pop(RESTARTED_ENVIRONMENT_VARIABLE, None) is not None
