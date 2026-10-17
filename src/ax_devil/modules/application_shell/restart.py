"""Restart ax-devil with its global options, without the command line's content."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QCoreApplication, QProcess
from PySide6.QtWidgets import QApplication, QWidget

from ax_devil.launcher import launch_command
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_RESTART_PROPERTY = "axDevilRestartRequested"


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


def relaunch_if_requested(app: QCoreApplication, global_options: Sequence[str]) -> None:
    """Start ax-devil again with *global_options* when a restart was requested; call last on exit.

    The command line's subcommand and content are left out, so the new process has nothing to open and lands on the
    start panel; unsaved edits are among the recent workspaces. It starts through the launcher that started this
    process, so it runs the current plugin selection.
    """
    if not app.property(_RESTART_PROPERTY):
        return
    app.setProperty(_RESTART_PROPERTY, False)
    program, *arguments = [*launch_command(), *global_options]
    started, _pid = QProcess.startDetached(program, arguments)
    if not started:
        logger.error(f"Could not restart ax-devil with {program}")
