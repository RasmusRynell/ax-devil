"""The plugin package documented in the write-plugin skill: discovery, decoding and a real installation."""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ax_devil.modules.plugin_installation import project
from ax_devil.modules.plugin_installation.host import current_host


def _decode_documented_plugin(interpreter: str) -> subprocess.CompletedProcess[str]:
    probe = (
        "from ax_devil.modules.plugin_installation.validate import validate_plugins; "
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
    source.write_text(source.read_text().replace("return 3\n", "return 999\n"))
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
            "from ax_devil.modules.plugin_installation.validate import validate_plugins; "
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
