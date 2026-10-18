"""Pre-Qt launch routing: running the prepared environment, refreshing plugins and bypassing a broken one."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import ANY, Mock

import pytest
from click.testing import CliRunner

from ax_devil import launcher
from ax_devil.modules.plugin_installation import project
from ax_devil.modules.plugin_installation.cli import plugins
from ax_devil.modules.plugin_installation.host import current_host


def test_launch_preserves_arguments(plugin: Path, uv: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Launch executes the prepared Python directly and bypasses the launcher on re-entry."""
    project.change_plugins(current_host(), install=(str(plugin),))
    arguments = ["--config", "/tmp/config with spaces.json", "local", "--video", "movie.mp4"]
    monkeypatch.setattr(sys, "orig_argv", ["/venv/bin/python", "/venv/bin/ax-devil", *arguments])
    monkeypatch.setattr(sys, "argv", ["/venv/bin/ax-devil", *arguments])
    execute = Mock(side_effect=SystemExit(0))
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(SystemExit):
        launcher.main()
    interpreter = str(project.runtime_python(current_host()))
    execute.assert_called_once_with(interpreter, [interpreter, "-I", "-m", "ax_devil.cli", *arguments], ANY)
    # The plugin interpreter restarts and manages plugins through the launcher, in the base app.
    environment = execute.call_args.args[2]
    monkeypatch.setattr(os, "environ", environment)
    assert launcher.launch_command() == ["/venv/bin/python", "/venv/bin/ax-devil"]
    assert launcher.plugin_root() == project.installation_root(current_host())


@pytest.mark.parametrize("refreshes", [True, False])
def test_launch_refreshes_outdated_plugins(
    plugin: Path, uv: Mock, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refreshes: bool
) -> None:
    """After an app or dependency upgrade, launch rebuilds the same plugins or falls back to the base app."""
    from ax_devil import cli

    project.change_plugins(current_host(), install=(str(plugin),))
    upgraded = replace(current_host(), constraints=("example-dependency==2.0",))
    monkeypatch.setattr(launcher, "current_host", Mock(return_value=upgraded))
    monkeypatch.setattr(sys, "argv", ["ax-devil"])
    execute = Mock(side_effect=SystemExit(0))
    monkeypatch.setattr(os, "execve", execute)
    base = Mock()
    monkeypatch.setattr(cli, "cli", base)
    succeed = uv.side_effect
    if not refreshes:
        uv.side_effect = subprocess.CalledProcessError(1, ["uv", "sync"])
    with pytest.raises(SystemExit) if refreshes else nullcontext():
        launcher.main()
    error = capsys.readouterr().err
    assert "updating plugins" in error
    if refreshes:
        interpreter = str(project.runtime_python(upgraded))
        execute.assert_called_once_with(interpreter, [interpreter, "-I", "-m", "ax_devil.cli"], ANY)
        current = project.installation_root(upgraded) / "current"
        assert project.read_project(current)["tool"]["uv"]["constraint-dependencies"] == ["example-dependency==2.0"]
    else:
        execute.assert_not_called()
        base.assert_called_once()
        assert "Repair with: uv run ax-devil plugins update" in error
        # The failure is remembered: later launches start immediately until an explicit update.
        calls = uv.call_count
        launcher.main()
        assert uv.call_count == calls
        assert "failed earlier" in capsys.readouterr().err
        uv.side_effect = succeed
        project.change_plugins(upgraded)
        assert project.runtime_python(upgraded) is not None


def test_refresh_after_concurrent_refresh_does_nothing(plugin: Path, uv: Mock) -> None:
    """A launch that waited for another launch's refresh does not rebuild the same environment again."""
    project.change_plugins(current_host(), install=(str(plugin),))
    upgraded = replace(current_host(), constraints=("example-dependency==2.0",))
    project.refresh_plugins(upgraded)
    calls = uv.call_count
    project.change_plugins(upgraded, refresh=True)
    assert uv.call_count == calls


@pytest.mark.parametrize("options", [[], ["--debug"], ["--log-level", "DEBUG"], ["--config", "plugins"]])
def test_management_bypasses_broken_runtime(plugin: Path, monkeypatch: pytest.MonkeyPatch, options: list[str]) -> None:
    """The public plugin command stays usable without inspecting the application environment."""
    monkeypatch.chdir(plugin.parent)
    monkeypatch.setattr(sys, "argv", ["ax-devil", *options, "plugins", "list"])
    inspect = Mock(side_effect=AssertionError("must not inspect runtime"))
    monkeypatch.setattr(launcher, "runtime_python", inspect)
    with pytest.raises(SystemExit) as result:
        launcher.main()
    assert result.value.code == 0
    inspect.assert_not_called()


@pytest.mark.parametrize("metadata", ['[project]\nname="bad-plugin"\nentry-points=[]', "project=[]"])
def test_invalid_plugin_metadata_is_a_cli_error(plugin: Path, uv: Mock, metadata: str) -> None:
    """Malformed package tables should produce an actionable error without running uv."""
    (plugin / "pyproject.toml").write_text(metadata)
    result = CliRunner().invoke(plugins, ["install", str(plugin)])
    assert result.exit_code == 1
    assert "Error: Invalid plugin package metadata" in result.output
    assert str(plugin) in result.output
    uv.assert_not_called()


@pytest.mark.parametrize("options", [["--help"], ["--unknown"], ["--log-level", "INVALID"]])
def test_help_and_invalid_options_do_not_run_management(
    plugin: Path, monkeypatch: pytest.MonkeyPatch, options: list[str]
) -> None:
    """Help and invalid root options must never execute a following removal command."""
    from ax_devil import cli
    from ax_devil.modules.plugin_installation import cli as plugin_cli

    application = Mock()
    management = Mock()
    monkeypatch.setattr(cli, "cli", application)
    monkeypatch.setattr(plugin_cli, "plugins", management)
    monkeypatch.setattr(launcher, "runtime_python", Mock(return_value=None))
    monkeypatch.setattr(sys, "argv", ["ax-devil", *options, "plugins", "remove", "--all"])
    launcher.main()
    management.assert_not_called()
    application.assert_called_once_with(prog_name="ax-devil")


@pytest.mark.parametrize("missing", [False, True])
def test_base_fallback(plugin: Path, monkeypatch: pytest.MonkeyPatch, missing: bool) -> None:
    """No installation and unavailable installations both leave the base application usable."""
    from ax_devil import cli

    run = Mock()
    monkeypatch.setattr(cli, "cli", run)
    monkeypatch.setattr(sys, "argv", ["ax-devil"])
    monkeypatch.setattr(
        launcher, "runtime_python", Mock(side_effect=OSError("missing")) if missing else Mock(return_value=None)
    )
    launcher.main()
    run.assert_called_once()


def test_launcher_imports_no_qt() -> None:
    """Environment selection must happen before Qt or plugin imports in a fresh interpreter."""
    subprocess.run(
        [sys.executable, "-c", "import ax_devil.launcher, sys; assert 'PySide6' not in sys.modules"], check=True
    )


@pytest.mark.parametrize("arguments", [["--help"], ["plugins", "--help"]])
def test_base_and_plugin_help_without_unix_locking(plugin: Path, arguments: list[str]) -> None:
    """An unavailable fcntl module must not prevent base launch or management help."""
    script = (
        "import sys; sys.modules['fcntl'] = None; from ax_devil.launcher import main; sys.argv[0] = 'ax-devil'; main()"
    )
    result = subprocess.run([sys.executable, "-c", script, *arguments], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "Usage: ax-devil" in result.stdout


def test_plugin_changes_without_unix_locking_report_error(plugin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported locking is reported before starting uv or creating an installation."""
    monkeypatch.setitem(sys.modules, "fcntl", None)
    runner = Mock(side_effect=AssertionError("uv must not run"))
    monkeypatch.setattr(subprocess, "run", runner)
    result = CliRunner().invoke(plugins, ["install", str(plugin)])
    assert result.exit_code == 1
    assert "Plugin installation requires Unix file locking" in result.output
    assert not project.installation_root(current_host()).exists()
    runner.assert_not_called()
