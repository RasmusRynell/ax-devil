"""Plugin changes run one at a time away from the GUI thread, and a successful one asks for a restart."""

from __future__ import annotations

import subprocess
import threading
from logging import DEBUG

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.plugin_manager import plugin_changes
from ax_devil.modules.plugin_manager.plugin_changes import (
    PluginChange,
    PluginChanges,
    failure_reason,
    run_plugins_command,
)


def test_each_change_runs_the_matching_plugins_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changes run the base app's ``plugins`` command; an update reinstalls the requirement it was selected with."""
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(plugin_changes, "launch_command", lambda: ["/venv/bin/python", "/venv/bin/ax-devil"])

    run_plugins_command(PluginChange.install("/home/me/my-plugin"))
    run_plugins_command(PluginChange.update("yolo-detections", "yolo-detections>=0.2"))
    run_plugins_command(PluginChange.remove("yolo-detections"))

    assert [command[2:] for command in commands] == [
        ["plugins", "install", "--", "/home/me/my-plugin"],
        ["plugins", "install", "--upgrade-package", "yolo-detections", "--", "yolo-detections>=0.2"],
        ["plugins", "remove", "--", "yolo-detections"],
    ]
    assert {tuple(command[:2]) for command in commands} == {("/venv/bin/python", "/venv/bin/ax-devil")}


def test_a_failed_command_reports_the_line_that_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = (
        "Resolved 40 packages\n"
        "  × No solution found when resolving dependencies:\n"
        "  ╰─▶ Because missing-plugin was not found in the package registry, the requirements are unsatisfiable.\n"
        "Error: Command '['/uv', 'sync']' returned non-zero exit status 1.\n"
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, output="", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(plugin_changes.PluginChangeError) as failure:
        run_plugins_command(PluginChange.install("missing-plugin"))

    assert f"{failure.value}" == (
        "Because missing-plugin was not found in the package registry, the requirements are unsatisfiable."
    )


def test_a_failure_the_command_reports_itself_is_shown_without_its_prefix() -> None:
    assert failure_reason("Error: Plugin is not installed: yolo\n") == "Plugin is not installed: yolo"
    assert failure_reason("") == ""


def test_a_successful_change_makes_a_restart_needed(qtbot: QtBot) -> None:
    made: list[PluginChange] = []
    changes = PluginChanges(made.append)
    assert not changes.restart_needed

    change = PluginChange.remove("yolo-detections")
    with qtbot.waitSignal(changes.finished, timeout=5000) as finished:
        assert changes.start(change)

    assert made == [change]
    assert finished.args == [change, ""]
    assert changes.restart_needed
    assert changes.running is None


def test_a_failed_change_reports_its_reason_and_needs_no_restart(qtbot: QtBot) -> None:
    def fail(_change: PluginChange) -> None:
        raise plugin_changes.PluginChangeError("Plugin is not installed: yolo")

    changes = PluginChanges(fail)
    change = PluginChange.remove("yolo")
    with qtbot.waitSignal(changes.finished, timeout=5000) as finished:
        changes.start(change)

    assert finished.args == [change, "Plugin is not installed: yolo"]
    assert not changes.restart_needed


def test_a_second_change_is_refused_while_one_runs(qtbot: QtBot) -> None:
    release = threading.Event()
    made: list[PluginChange] = []

    def install(change: PluginChange) -> None:
        release.wait(5)
        made.append(change)

    changes = PluginChanges(install)
    first = PluginChange.install("first-plugin")
    assert changes.start(first)
    assert changes.running == first
    assert not changes.start(PluginChange.install("second-plugin"))

    with qtbot.waitSignal(changes.finished, timeout=5000):
        release.set()
    assert made == [first]

    second = PluginChange.install("second-plugin")
    with qtbot.waitSignal(changes.finished, timeout=5000):
        assert changes.start(second)
    assert made == [first, second]


def test_a_source_that_looks_like_an_option_is_installed_as_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the user types in Add plugin reaches the installer as a requirement, whatever it starts with."""
    from click.testing import CliRunner

    from ax_devil.modules.plugin_installation import cli

    installed: list[tuple[str, ...]] = []
    monkeypatch.setattr(cli, "current_host", lambda: object())
    monkeypatch.setattr(cli, "change_plugins", lambda _host, *, install, **_kwargs: installed.append(install))

    change = PluginChange.install("--index=evil=https://example.invalid")
    result = CliRunner().invoke(cli.plugins, list(change.arguments))

    assert result.exit_code == 0, result.output
    assert installed == [("--index=evil=https://example.invalid",)]


def test_a_failed_install_never_logs_or_shows_the_credentials_of_its_source(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    source = "private-plugin @ https://user:s3cr3t-token@example.com/private-plugin.whl?token=q-s3cr3t"

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, output="", stderr=f"Error: Failed to fetch: {source}\n")

    monkeypatch.setattr(subprocess, "run", run)
    changes = PluginChanges(run_plugins_command)
    with caplog.at_level(DEBUG), qtbot.waitSignal(changes.finished, timeout=5000) as finished:
        changes.start(PluginChange.install(source))

    reason = finished.args[1]
    assert reason
    assert "s3cr3t-token" not in reason
    assert "s3cr3t-token" not in caplog.text
    assert "q-s3cr3t" not in reason
    assert "q-s3cr3t" not in caplog.text
    assert "example.com" in caplog.text  # The log still says what failed.
