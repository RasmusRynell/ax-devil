"""Install, update and remove plugins from the running app, one change at a time, away from the GUI thread."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, Signal

from ax_devil.launcher import launch_command
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class ChangeKind(Enum):
    """What a change does: the ``plugins`` command it runs and what is shown while it runs."""

    INSTALL = (("install",), "Installing…")
    UPDATE = (("install", "--upgrade"), "Updating…")
    REMOVE = (("remove",), "Removing…")

    def __init__(self, command: tuple[str, ...], progress: str) -> None:
        self.command = command
        self.progress = progress


@dataclass(frozen=True)
class PluginChange:
    """One change to the plugin selection.

    *target* is what the command takes: the requirement, wheel or folder to install, or the package to remove.
    *name* is the package the change is about, when it is known before the change runs.
    """

    kind: ChangeKind
    target: str
    name: str = ""

    @classmethod
    def install(cls, source: str) -> PluginChange:
        """Install a package requirement, a wheel or a plugin source folder."""
        return cls(ChangeKind.INSTALL, source)

    @classmethod
    def update(cls, name: str, requirement: str) -> PluginChange:
        """Reinstall the package *name* from the *requirement* it was selected with, allowing newer versions."""
        return cls(ChangeKind.UPDATE, requirement, name)

    @classmethod
    def remove(cls, name: str) -> PluginChange:
        """Remove the package *name*."""
        return cls(ChangeKind.REMOVE, name, name)

    @property
    def arguments(self) -> tuple[str, ...]:
        """Return the arguments of ``ax-devil plugins`` that make this change."""
        return (*self.kind.command, self.target)


Installer = Callable[[PluginChange], None]
"""Make a change, blocking until it is done; raise with a short reason when it fails."""


class PluginChangeError(Exception):
    """A plugin change failed, for the reason in the message."""


def run_plugins_command(change: PluginChange) -> None:
    """Make *change* with the ``plugins`` command of the app's launcher.

    Plugins are managed by the base app, never by the plugin environment this process may run in.
    """
    command = [*launch_command(), "plugins", *change.arguments]
    try:
        subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        logger.error(f"Plugin change failed: {' '.join(command)}\n{exc.stderr}")
        raise PluginChangeError(failure_reason(f"{exc.stdout or ''}\n{exc.stderr or ''}") or f"{exc}") from exc


def failure_reason(output: str) -> str:
    """Return the line of a failed ``plugins`` command's *output* that says why it failed, or an empty string.

    That is the last line, unless it only repeats that a step such as ``uv sync`` failed; then it is the step's own
    last line above it.
    """
    lines = [line.strip().removeprefix("Error: ") for line in output.splitlines() if line.strip()]
    while len(lines) > 1 and lines[-1].startswith("Command ") and "returned non-zero exit status" in lines[-1]:
        lines.pop()
    return lines[-1].lstrip("×╰─▶ ") if lines else ""


class _Run(QObject):
    """One change on its way back to the GUI thread; created there, so its signal is queued to the receiver."""

    finished = Signal(str)  # The failure reason, empty on success.


class PluginChanges(QObject):
    """Run one plugin change at a time on a worker thread and report how it ended on the GUI thread.

    The changed plugins load the next time the app starts, so a change that succeeds makes a restart needed.
    """

    started = Signal(object)  # PluginChange
    finished = Signal(object, str)  # PluginChange, the failure reason or an empty string on success

    def __init__(self, installer: Installer = run_plugins_command, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._installer = installer
        self._running: PluginChange | None = None
        self._run: _Run | None = None
        self._restart_needed = False

    @property
    def running(self) -> PluginChange | None:
        """Return the change that is running, if any."""
        return self._running

    @property
    def restart_needed(self) -> bool:
        """Return whether a change succeeded since the app started."""
        return self._restart_needed

    def start(self, change: PluginChange) -> bool:
        """Start *change*; refuse it, returning False, while another change runs."""
        if self._running is not None:
            return False
        self._running = change
        run = _Run()
        run.finished.connect(self._on_finished)
        self._run = run
        threading.Thread(target=_make, args=(self._installer, change, run), name="plugin-change", daemon=True).start()
        self.started.emit(change)
        return True

    def _on_finished(self, reason: str) -> None:
        change, run = self._running, self._run
        self._running = self._run = None
        if run is not None:
            run.deleteLater()  # The worker may still hold the wrapper; native destruction belongs to the GUI thread.
        if change is None:
            return
        if not reason:
            self._restart_needed = True
        self.finished.emit(change, reason)


def _make(installer: Installer, change: PluginChange, run: _Run) -> None:
    """Make *change* on the worker thread and send back how it ended."""
    try:
        installer(change)
        reason = ""
    except Exception as exc:
        logger.exception(f"Plugin change failed: {' '.join(change.arguments)}")
        reason = f"{exc}" or type(exc).__name__
    run.finished.emit(reason)
