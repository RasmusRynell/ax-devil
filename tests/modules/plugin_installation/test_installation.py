"""Installation transactions and pre-Qt launch routing."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from uv import find_uv_bin

from ax_devil import launcher
from ax_devil.modules.plugin_installation import project
from ax_devil.modules.plugin_installation.cli import plugins
from ax_devil.modules.plugin_installation.host import Host, current_host


def test_installed_host_ignores_working_directory(plugin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A release uses its base environment, even when launched inside a checkout."""
    distribution = Mock(version="1.2.3", requires=None)
    distribution.read_text.return_value = None
    monkeypatch.setattr(importlib.metadata, "distribution", Mock(return_value=distribution))
    monkeypatch.chdir(plugin)
    installed = current_host()
    assert (installed.location, installed.version, installed.editable) == (Path(sys.prefix).absolute(), "1.2.3", False)
    assert installed.requirement == "ax-devil==1.2.3"
    assert installed.command == "ax-devil"
    assert project.runtime_python(installed) is None


@pytest.mark.parametrize(
    ("direct_url", "requirement"),
    [
        (
            {"url": "file:///downloads/ax_devil-1.2.3-py3-none-any.whl", "archive_info": {}},
            "ax-devil @ file:///downloads/ax_devil-1.2.3-py3-none-any.whl",
        ),
        (
            {"url": "https://example.com/ax-devil.git", "vcs_info": {"vcs": "git", "commit_id": "abc123"}},
            "ax-devil @ git+https://example.com/ax-devil.git@abc123",
        ),
        (
            {
                "url": "https://example.com/tools.git",
                "vcs_info": {"vcs": "git", "commit_id": "abc123"},
                "subdirectory": "ax-devil",
            },
            "ax-devil @ git+https://example.com/tools.git@abc123#subdirectory=ax-devil",
        ),
    ],
)
def test_direct_install_reuses_its_source(
    monkeypatch: pytest.MonkeyPatch, direct_url: dict[str, object], requirement: str
) -> None:
    """Apps installed from a wheel or repository give plugins that same app, without needing an index."""
    distribution = Mock(version="1.2.3", requires=None)
    distribution.read_text.return_value = json.dumps(direct_url)
    monkeypatch.setattr(importlib.metadata, "distribution", Mock(return_value=distribution))
    assert current_host().requirement == requirement


def test_directory_install_is_rejected_for_plugins(tmp_path: Path) -> None:
    """A non-editable directory install can change under the same version, so plugins would run other app code."""
    host = Host(tmp_path / "tool", "1.2.3", source=tmp_path.as_uri())
    with pytest.raises(ValueError, match="installed editable, from a wheel"):
        host.requirement


def test_python_identity_ignores_managed_python_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """uv reaches one managed Python through a minor-version link or its patch directory; both are one interpreter."""
    patch = tmp_path / "cpython-3.10.22"
    patch.mkdir()
    link = tmp_path / "cpython-3.10"
    link.symlink_to(patch)
    host = Host(tmp_path / "tool", "1.0")
    monkeypatch.setattr(sys, "base_prefix", str(patch))
    fingerprint = host.fingerprint
    monkeypatch.setattr(sys, "base_prefix", str(link))
    assert host.fingerprint == fingerprint


def test_host_pins_installed_dependencies() -> None:
    """Plugins must run on the dependency versions the app was installed with."""
    pinned = {constraint.split("==")[0] for constraint in current_host().constraints}
    assert f"pyside6=={importlib.metadata.version('PySide6')}" in current_host().constraints
    # Only the app's dependency closure: neither the app itself nor development tools beside it.
    assert {"pyside6-essentials", "paho-mqtt"} <= pinned
    assert not pinned & {"ax-devil", "pytest", "mypy", "ruff"}


def test_release_upgrade_preserves_selection_and_requires_refresh(plugin: Path, uv: Mock) -> None:
    """A new app must never execute the old app bundled in the plugin environment."""
    host = Host(plugin.parent / "tool", "1.0")
    project.change_plugins(host, install=("example-plugin>=0.1",))
    current = project.installation_root(host) / "current"
    upgraded = replace(host, version="2.0")
    assert project.installation_root(upgraded) == current.parent
    with pytest.raises(ValueError, match="needs an update"):
        project.runtime_python(upgraded)
    assert project.selected_plugins(current) == {"example-plugin": "example-plugin>=0.1"}
    project.change_plugins(upgraded, upgrade=True)
    assert project.runtime_python(upgraded) is not None
    assert project.read_project(current)["project"]["dependencies"] == ["ax-devil==2.0", "example-plugin>=0.1"]


@pytest.mark.parametrize("change", ["python", "metadata"])
def test_development_runtime_invalidated_by_environment_changes(
    plugin: Path, uv: Mock, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Source edits remain editable, but interpreter or dependency changes require refresh."""
    checkout = plugin.parent / "checkout"
    checkout.mkdir()
    metadata = checkout / "pyproject.toml"
    metadata.write_text('[project]\nname="ax-devil"\nversion="1.0"\n')
    host = Host(checkout, "1.0", editable=True)
    project.change_plugins(host, install=(str(plugin),))
    if change == "python":
        monkeypatch.setattr(sys, "version", "changed interpreter")
    else:
        metadata.write_text(f'{metadata.read_text()}dependencies=["example>=2"]\n')
    with pytest.raises(ValueError, match="needs an update"):
        project.runtime_python(host)
    project.change_plugins(host)
    assert project.runtime_python(host) is not None


def test_wheel_selection_survives_update(plugin: Path, uv: Mock) -> None:
    """Wheel paths are retained as file requirements, never installed as editable sources."""
    wheel = plugin.parent / "example_plugin-0.1.0-py3-none-any.whl"
    wheel.touch()
    host = current_host()
    project.change_plugins(host, install=(str(wheel),))
    current = project.installation_root(host) / "current"
    project.change_plugins(host)
    assert project.selected_plugins(current) == {"example-plugin": f"example-plugin @ {wheel.as_uri()}"}
    assert "example-plugin" not in project.read_project(current)["tool"]["uv"]["sources"]


@pytest.mark.parametrize("requirement", ["ax-devil>=1", "example; python_version<'3.11'", "./missing-plugin"])
def test_invalid_selection_does_not_run_installer(plugin: Path, uv: Mock, requirement: str) -> None:
    """Reject app upgrades, conditional selections, and missing paths before resolution."""
    result = CliRunner().invoke(plugins, ["install", requirement])
    assert result.exit_code == 1
    uv.assert_not_called()


@pytest.fixture
def uv(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Simulate successful environment preparation without package downloads."""
    pytest.importorskip("fcntl")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == [find_uv_bin(), "sync"]:
            directory = Path(command[command.index("--project") + 1])
            (directory / ".venv/bin").mkdir(parents=True)
            (directory / ".venv/bin/python").touch()
            (directory / "uv.lock").write_text("version = 1\n")
        return subprocess.CompletedProcess(command, 0)

    runner = Mock(side_effect=run)
    monkeypatch.setattr(subprocess, "run", runner)
    return runner


@pytest.mark.parametrize("upgrade", [False, True])
def test_prepare_uses_isolated_editable_project(
    plugin: Path, uv: Mock, monkeypatch: pytest.MonkeyPatch, upgrade: bool
) -> None:
    """Prepare the selected sources in a separate environment and validate them before use."""
    host = current_host()
    monkeypatch.setenv("VIRTUAL_ENV", str(host.location / ".venv"))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(host.location / ".venv"))
    monkeypatch.setenv("UV_NO_EDITABLE", "true")
    monkeypatch.setenv("UV_NO_SOURCES", "true")
    monkeypatch.setenv("UV_NO_SOURCES_PACKAGE", "ax-devil")
    project.change_plugins(host, install=(str(plugin),), upgrade=upgrade)
    prepared = (project.installation_root(host) / "current").resolve()

    metadata = project.read_project(prepared)
    assert metadata["project"]["dependencies"] == ["ax-devil", "example-plugin"]
    assert metadata["project"]["requires-python"] == f"=={sys.version_info.major}.{sys.version_info.minor}.*"
    assert metadata["tool"]["uv"]["constraint-dependencies"] == list(host.constraints)
    assert metadata["tool"]["uv"]["sources"] == {
        "ax-devil": {"path": str(host.location), "editable": True},
        "example-plugin": {"path": str(plugin), "editable": True},
    }
    sync, validate = uv.call_args_list
    assert sync.args[0] == [
        find_uv_bin(),
        "sync",
        "--project",
        str(prepared),
        "--python",
        sys.executable,
        "--no-default-groups",
        *(["--upgrade"] if upgrade else []),
    ]
    environment = sync.kwargs["env"]
    assert environment["UV_PROJECT_ENVIRONMENT"] == str(prepared / ".venv")
    assert "VIRTUAL_ENV" not in environment
    assert "UV_NO_EDITABLE" not in environment
    assert "UV_NO_SOURCES" not in environment
    assert "UV_NO_SOURCES_PACKAGE" not in environment
    assert sync.kwargs["check"] is True
    assert validate.args[0] == [
        str(prepared / ".venv/bin/python"),
        "-I",
        "-m",
        "ax_devil.modules.plugin_installation.validate",
        str(prepared),
    ]
    assert validate.kwargs == {"env": {**environment, "QT_QPA_PLATFORM": "offscreen"}, "check": True}


def test_install_remove_and_lock_reuse(plugin: Path, uv: Mock) -> None:
    """Host files stay unchanged; replacement installations reuse the uv lock."""
    host = current_host()
    before = {name: (host.location / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    project.change_plugins(host, install=(str(plugin),))
    current = project.installation_root(host) / "current"
    original = current.resolve()
    assert project.selected_plugins(current) == {"example-plugin": str(plugin)}
    assert project.runtime_python(host) == original / ".venv/bin/python"
    assert uv.call_args_list[1].args[0][-1] == str(original)
    project.change_plugins(host)
    assert current.resolve() != original
    assert original.is_dir()
    assert (current / "uv.lock").read_bytes() == (original / "uv.lock").read_bytes()
    assert all((host.location / name).read_bytes() == content for name, content in before.items())
    replaced = current.resolve()
    project.change_plugins(host)
    assert replaced.is_dir()
    assert not original.exists(), "only the most recently replaced environment is kept"
    project.change_plugins(host, remove=("EXAMPLE_plugin",))
    assert project.runtime_python(host) is None
    project.change_plugins(host, clear=True)
    assert not list(project.installation_root(host).glob("project-*"))


def test_private_index_is_scoped_to_plugin_project_and_preserved(plugin: Path, uv: Mock) -> None:
    """A named index is stored only in the private project and survives updates."""
    host = current_host()
    before = (host.location / "uv.lock").read_bytes()
    result = CliRunner().invoke(
        plugins,
        ["install", "--index", "axis=https://packages.example/simple", str(plugin)],
    )
    assert result.exit_code == 0, result.output
    current = project.installation_root(host) / "current"
    assert project.selected_indexes(current) == {"axis": "https://packages.example/simple"}
    project.change_plugins(host)
    assert project.selected_indexes(current) == {"axis": "https://packages.example/simple"}
    assert (host.location / "uv.lock").read_bytes() == before


def test_add_and_remove_preserve_other_plugins(plugin: Path, uv: Mock, tmp_path: Path) -> None:
    """Adding or removing one distribution preserves the remaining selection."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "pyproject.toml").write_text(
        (plugin / "pyproject.toml").read_text().replace("example-plugin", "other-plugin")
    )
    host = current_host()
    project.change_plugins(host, install=(str(plugin),))
    project.change_plugins(host, install=(str(other),))
    current = project.installation_root(host) / "current"
    assert set(project.selected_plugins(current)) == {"example-plugin", "other-plugin"}
    project.change_plugins(host, remove=("example-plugin",))
    assert project.selected_plugins(current) == {"other-plugin": str(other)}


@pytest.mark.parametrize("failed_step", [0, 1])
def test_failed_preparation_preserves_current(plugin: Path, uv: Mock, failed_step: int) -> None:
    """Both resolver and real-plugin validation failures preserve the previous installation."""
    host = current_host()
    project.change_plugins(host, install=(str(plugin),))
    current = project.installation_root(host) / "current"
    original = current.resolve()
    success = uv.side_effect
    calls = 0

    def fail(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        step = calls
        calls += 1
        if step == failed_step:
            raise subprocess.CalledProcessError(1, command)
        result: subprocess.CompletedProcess[str] = success(command, **kwargs)
        return result

    uv.side_effect = fail
    with pytest.raises(subprocess.CalledProcessError):
        project.change_plugins(host, upgrade=True)
    assert current.resolve() == original
    assert list(project.installation_root(host).glob("project-*")) == [original]


def test_repair_missing_environment_and_clear_bad_metadata(plugin: Path, uv: Mock) -> None:
    """Management reads selection independently of the prepared interpreter."""
    host = current_host()
    project.change_plugins(host, install=(str(plugin),))
    current = project.installation_root(host) / "current"
    (current / ".venv/bin/python").unlink()
    with pytest.raises(ValueError, match="environment is missing"):
        project.runtime_python(host)
    project.change_plugins(host)
    assert project.runtime_python(host) is not None
    (current / "pyproject.toml").write_text("broken [")
    result = CliRunner().invoke(plugins, ["remove", "--all"])
    assert result.exit_code == 0, result.output
    assert project.runtime_python(host) is None


def test_concurrent_install_is_rejected(plugin: Path, uv: Mock) -> None:
    """Concurrent changes cannot overwrite each other's selection."""
    fcntl = pytest.importorskip("fcntl")
    host = current_host()
    root = project.installation_root(host)
    root.mkdir(parents=True)
    with (root / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="Another plugin"):
            project.change_plugins(host, install=(str(plugin),))
    uv.assert_not_called()


def test_launch_preserves_arguments(plugin: Path, uv: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    """Launch executes the prepared Python directly and bypasses the launcher on re-entry."""
    project.change_plugins(current_host(), install=(str(plugin),))
    arguments = ["--config", "/tmp/config with spaces.json", "local", "--video", "movie.mp4"]
    monkeypatch.setattr(sys, "argv", ["ax-devil", *arguments])
    execute = Mock(side_effect=SystemExit(0))
    monkeypatch.setattr(os, "execv", execute)
    with pytest.raises(SystemExit):
        launcher.main()
    interpreter = str(project.runtime_python(current_host()))
    execute.assert_called_once_with(interpreter, [interpreter, "-I", "-m", "ax_devil.cli", *arguments])


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
    monkeypatch.setattr(os, "execv", execute)
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
        execute.assert_called_once_with(interpreter, [interpreter, "-I", "-m", "ax_devil.cli"])
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


def _decode_documented_plugin(interpreter: str) -> subprocess.CompletedProcess[str]:
    probe = (
        "from ax_devil.modules.plugin_system.validate import validate_plugins; "
        "from ax_devil.modules.plugin_system import get_payload_decoder; "
        "validate_plugins(['example-plugin']); "
        "assert get_payload_decoder('EXAMPLE_FRAME').decode({'frame': 42}).time_slice.start == 42"
    )
    return subprocess.run([interpreter, "-c", probe], text=True, capture_output=True, timeout=30)


@pytest.fixture
def discoverable_plugin(plugin: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Expose the documented entry points and a tiny dependency through real package metadata."""
    metadata = project.read_project(plugin)["project"]
    distribution = plugin / f"{metadata['name'].replace('-', '_')}-{metadata['version']}.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {metadata['name']}\nVersion: {metadata['version']}\n"
    )
    entries = []
    for group, values in metadata["entry-points"].items():
        entries.append(f"[{group}]")
        entries.extend(f"{name} = {value}" for name, value in values.items())
    (distribution / "entry_points.txt").write_text("\n".join(entries))
    (plugin / "example_extra.py").write_text("VALUE = 42\n")
    source = plugin / "example_plugin.py"
    source.write_text(f"{source.read_text()}\nfrom example_extra import VALUE\nassert VALUE == 42\n")
    monkeypatch.setenv("PYTHONPATH", str(plugin))
    return plugin


def test_documented_plugin_discovery_and_decoding(discoverable_plugin: Path) -> None:
    """Discover the documented entry point and decode with its dependency in a fresh process."""
    result = _decode_documented_plugin(sys.executable)
    assert result.returncode == 0, result.stderr


def test_documented_plugin_missing_dependency(discoverable_plugin: Path) -> None:
    """Reject a plugin whose dependency cannot be imported."""
    (discoverable_plugin / "example_extra.py").unlink()
    result = _decode_documented_plugin(sys.executable)
    assert result.returncode == 1, result.stderr
    assert "No module named 'example_extra'" in result.stderr


def test_documented_plugin_incompatible_api(discoverable_plugin: Path) -> None:
    """Reject an installed entry point requiring an incompatible host API."""
    source = discoverable_plugin / "example_plugin.py"
    source.write_text(source.read_text().replace("return 1\n", "return 999\n"))
    result = _decode_documented_plugin(sys.executable)
    assert result.returncode == 1, result.stderr
    assert "requires plugin API 999" in result.stderr


@pytest.mark.integration
def test_real_documented_plugin_installation(plugin: Path) -> None:
    """Install the documented package with uv, then validate and decode through it."""
    host = current_host()
    before = {name: (host.location / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    packages = sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())
    environment = {**os.environ, "UV_NO_EDITABLE": "true", "UV_NO_SOURCES": "true", "UV_NO_SOURCES_PACKAGE": "ax-devil"}
    result = subprocess.run(
        ["uv", "run", "--no-sync", "ax-devil", "plugins", "install", str(plugin)],
        cwd=current_host().location,
        env=environment,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    interpreter = project.runtime_python(current_host())
    assert interpreter is not None
    result = _decode_documented_plugin(str(interpreter))
    assert result.returncode == 0, result.stderr
    source = plugin / "example_plugin.py"
    source.write_text(source.read_text().replace('frame = int(payload["frame"])', 'frame = int(payload["frame"]) + 1'))
    changed = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-c",
            "from ax_devil.modules.plugin_system.validate import validate_plugins; "
            "from ax_devil.modules.plugin_system import get_payload_decoder; "
            "validate_plugins(['example-plugin']); "
            "assert get_payload_decoder('EXAMPLE_FRAME').decode({'frame': 42}).time_slice.start == 43",
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert changed.returncode == 0, changed.stderr
    assert all((host.location / name).read_bytes() == content for name, content in before.items())
    assert sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()) == packages
