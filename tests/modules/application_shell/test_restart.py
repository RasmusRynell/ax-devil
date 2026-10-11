"""Restarting keeps the global options but leaves out the command line's content, so the kept workspace is restored."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from ax_devil.modules.application_shell import restart


def test_a_restart_relaunches_with_global_options_and_without_content(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[str, list[str]]] = []

    def start_detached(program: str, arguments: list[str]) -> tuple[bool, int]:
        started.append((program, arguments))
        return True, 1

    monkeypatch.delenv("AX_DEVIL_LAUNCH_COMMAND", raising=False)
    monkeypatch.setattr(sys, "orig_argv", ["python", "-I", "-m", "ax_devil.cli", "local", "--video", "clip.mp4"])
    monkeypatch.setattr(sys, "argv", ["/path/ax_devil/cli.py", "local", "--video", "clip.mp4"])
    monkeypatch.setattr(QProcess, "startDetached", start_detached)
    qapp.setProperty("axDevilRestartRequested", True)

    restart.relaunch_if_requested(qapp, ["--log-level", "DEBUG", "--config", "/cfg.toml"])

    assert started == [("python", ["-I", "-m", "ax_devil.cli", "--log-level", "DEBUG", "--config", "/cfg.toml"])]
    restart.relaunch_if_requested(qapp, [])
    assert len(started) == 1, "the request is cleared once it has relaunched"


def test_a_restart_from_the_plugin_environment_goes_through_the_launcher(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launcher picks the plugin environment again, so a restart runs plugins installed since this one started."""
    started: list[tuple[str, list[str]]] = []

    def start_detached(program: str, arguments: list[str]) -> tuple[bool, int]:
        started.append((program, arguments))
        return True, 1

    monkeypatch.setenv("AX_DEVIL_LAUNCH_COMMAND", '["/venv/bin/python", "/venv/bin/ax-devil"]')
    monkeypatch.setattr(sys, "orig_argv", ["/plugins/project-1/.venv/bin/python", "-I", "-m", "ax_devil.cli"])
    monkeypatch.setattr(sys, "argv", ["/path/ax_devil/cli.py"])
    monkeypatch.setattr(QProcess, "startDetached", start_detached)
    qapp.setProperty("axDevilRestartRequested", True)

    restart.relaunch_if_requested(qapp, ["--debug"])

    assert started == [("/venv/bin/python", ["/venv/bin/ax-devil", "--debug"])]
