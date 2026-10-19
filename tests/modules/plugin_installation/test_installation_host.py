"""The application host that plugins install into: its identity, source and selected requirements."""

from __future__ import annotations

import importlib.metadata
import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

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
