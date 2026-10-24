"""Installation transactions: preparing, changing, repairing and locking the plugin environment."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from ax_devil.modules.plugin_installation import project
from ax_devil.modules.plugin_installation.cli import plugins
from ax_devil.modules.plugin_installation.host import current_host


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
    # uv must build the private project's own environment, never the base app's .venv it was launched from.
    sync, validate = uv.call_args_list
    command = sync.args[0]
    assert command[command.index("--project") + 1] == str(prepared)
    assert command[command.index("--python") + 1] == sys.executable
    assert ("--upgrade" in command) is upgrade
    environment = sync.kwargs["env"]
    assert environment["UV_PROJECT_ENVIRONMENT"] == str(prepared / ".venv")
    assert not {"VIRTUAL_ENV", "UV_NO_EDITABLE", "UV_NO_SOURCES", "UV_NO_SOURCES_PACKAGE"} & environment.keys()
    # The plugins are imported by the prepared interpreter, isolated from the caller, before activation.
    assert validate.args[0][0] == str(prepared / ".venv/bin/python")
    assert "-I" in validate.args[0]
    assert validate.args[0][-2] == str(prepared)


def test_installing_with_upgrade_upgrades_beyond_the_lock(plugin: Path, uv: Mock) -> None:
    """Updating one plugin from the app reinstalls its requirement and lets its dependencies move on."""
    result = CliRunner().invoke(plugins, ["install", "--upgrade", str(plugin)])
    assert result.exit_code == 0, result.output
    assert "--upgrade" in uv.call_args_list[0].args[0]
    assert project.selected_plugins(project.installation_root(current_host()) / "current") == {
        "example-plugin": str(plugin)
    }


def test_install_remove_and_lock_reuse(plugin: Path, uv: Mock) -> None:
    """Host files stay unchanged; replacement installations reuse the uv lock."""
    host = current_host()
    before = {name: (host.location / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    project.change_plugins(host, install=(str(plugin),))
    current = project.installation_root(host) / "current"
    original = current.resolve()
    assert project.selected_plugins(current) == {"example-plugin": str(plugin)}
    assert project.runtime_python(host) == original / ".venv/bin/python"
    assert uv.call_args_list[1].args[0][-2] == str(original)
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


@pytest.mark.parametrize(("failed_step", "writes_reason"), [(0, False), (1, True), (1, False)])
def test_failed_preparation_preserves_current(plugin: Path, uv: Mock, failed_step: int, writes_reason: bool) -> None:
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
            if writes_reason:  # The validator reports to the result file named after the project.
                Path(command[-1]).write_text("example-plugin cannot load: missing dependency:\nNo module named 'cv3'")
            return subprocess.CompletedProcess(command, 1)
        result: subprocess.CompletedProcess[str] = success(command, **kwargs)
        return result

    uv.side_effect = fail
    with pytest.raises(ValueError) as failure:
        project.change_plugins(host, upgrade=True)
    message = f"{failure.value}"
    assert "non-zero exit status" not in message
    if failed_step == 1 and writes_reason:
        assert "example-plugin cannot load: missing dependency: No module named 'cv3';" in message
    elif failed_step == 1:
        assert "plugin validation stopped with exit status 1" in message
    else:
        assert "example-plugin" in message
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
