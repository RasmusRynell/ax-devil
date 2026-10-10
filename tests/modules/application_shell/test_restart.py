"""Restarting keeps the command line's options but restores the kept workspace instead of its content."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from ax_devil.modules.application_shell import restart


def test_a_restarted_process_is_marked_to_restore_the_kept_workspace(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[str, list[str]]] = []

    def start_detached(program: str, arguments: list[str]) -> tuple[bool, int]:
        started.append((program, arguments))
        return True, 1

    monkeypatch.setattr(sys, "orig_argv", ["python", "ax-devil", "local", "--video", "clip.mp4"])
    monkeypatch.setattr(QProcess, "startDetached", start_detached)
    monkeypatch.delenv(restart.RESTARTED_ENVIRONMENT_VARIABLE, raising=False)
    qapp.setProperty("axDevilRestartRequested", True)

    restart.relaunch_if_requested(qapp)

    assert started == [("python", ["ax-devil", "local", "--video", "clip.mp4"])]
    assert restart.is_restarted_launch()
    assert not restart.is_restarted_launch(), "the mark is cleared so processes the restart starts are not marked"
