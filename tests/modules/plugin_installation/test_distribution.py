"""Real wheel/tool installations, plugin transactions, and app upgrades outside the checkout."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ax_devil.modules.plugin_installation.host import Host, current_host
from ax_devil.modules.plugin_installation.project import installation_root, read_project

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("installer", ["tool", "pip", "wheel"])
def test_distribution_lifecycle(plugin: Path, tmp_path: Path, installer: str) -> None:
    """Keep base installs intact across plugin upgrades, rejected changes, and app upgrades."""
    repository = current_host().location
    original_files = {name: (repository / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    working = tmp_path / "unrelated"
    working.mkdir()
    (working / "uv.py").write_text('raise RuntimeError("Installer imported caller directory")\n')
    shadow = working / "ax_devil"
    shadow.mkdir()
    (shadow / "__init__.py").write_text('raise RuntimeError("App imported caller directory")\n')
    pythonpath = tmp_path / "pythonpath"
    pythonpath.mkdir()
    (pythonpath / "PySide6.py").write_text('raise RuntimeError("App inherited PYTHONPATH")\n')
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    # A wheel-path install must work without any index or find-links that offer the app.
    app_wheels = tmp_path / "app-wheels" if installer == "wheel" else wheels
    app_wheels.mkdir(exist_ok=True)
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "UV_FIND_LINKS": str(wheels),
        "QT_QPA_PLATFORM": "offscreen",
        "QT_WIDGETS_RHI": "0",
    }
    for key in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "UV_PROJECT_ENVIRONMENT"):
        environment.pop(key, None)
    # Preserve the usual download cache when isolating HOME; do not copy credentials/config.
    environment["UV_CACHE_DIR"] = os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache/uv"))

    def run(*arguments: str, success: bool = True) -> subprocess.CompletedProcess[str]:
        # Desktop theme watchers can inherit output handles; wait for the app, not pipe EOF.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error:
                with subprocess.Popen(
                    arguments,
                    cwd=working,
                    env=environment,
                    text=True,
                    stdout=output,
                    stderr=error,
                    start_new_session=True,
                ) as process:
                    try:
                        returncode = process.wait(timeout=30 if "-I" in arguments else 240)
                    finally:
                        # Reap any theme monitor left behind by this test-owned process group.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                output.seek(0)
                error.seek(0)
                result = subprocess.CompletedProcess(arguments, returncode, output.read(), error.read())
        if success:
            assert result.returncode == 0, f"{arguments}\n{result.stdout}\n{result.stderr}"
        else:
            assert result.returncode != 0, f"Unexpected success: {arguments}"
        return result

    def build(source: Path, out: Path = wheels) -> Path:
        run(sys.executable, "-I", "-m", "uv", "build", "--wheel", str(source), "--out-dir", str(out))
        return max(out.glob("*.whl"), key=lambda wheel: wheel.stat().st_mtime_ns)

    source = tmp_path / "app-source"
    source.mkdir()
    for name in ("README.md", "LICENSE"):
        shutil.copyfile(repository / name, source / name)
    shutil.copytree(repository / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    metadata = original_files["pyproject.toml"].decode()

    def build_app(version: str) -> Path:
        (source / "pyproject.toml").write_text(
            metadata.replace(f'version = "{current_host().version}"', f'version = "{version}"', 1)
        )
        return build(source, app_wheels)

    app_wheel = build_app("9999.0.1")
    plugin_metadata = (
        (plugin / "pyproject.toml")
        .read_text()
        .replace("example-plugin", "ax-devil-smoke-plugin")
        .replace('dependencies = ["ax-devil"]', 'dependencies = ["ax-devil<9999.0.2"]')
    )
    (plugin / "pyproject.toml").write_text(plugin_metadata)
    build(plugin)
    if installer == "tool":
        run(sys.executable, "-I", "-m", "uv", "tool", "install", "--python", sys.executable, "ax-devil")
        base = tmp_path / "tools/ax-devil"
        command = str(tmp_path / "bin/ax-devil")
    else:
        base = tmp_path / "base"
        run(sys.executable, "-I", "-m", "uv", "venv", "--seed", "--python", sys.executable, str(base))
        target = str(app_wheel) if installer == "wheel" else "ax-devil"
        run(str(base / "bin/python"), "-m", "pip", "install", "--find-links", str(wheels), target)
        command = str(base / "bin/ax-devil")
    interpreter = str(base / "bin/python")
    # Probe is copied so neither cwd nor script directory can expose repository modules.
    probe = working / "probe.py"
    shutil.copyfile(repository / "tests/helpers/installed_app_probe.py", probe)

    def snapshot() -> str:
        return run(
            interpreter,
            "-I",
            "-c",
            "import importlib.metadata as m, json, sys; "
            "sys.stdout.write(json.dumps(sorted((d.metadata['Name'], d.version) for d in m.distributions())))",
        ).stdout

    before = snapshot()
    help_result = run(command, "--help")
    assert "Plugin installation unavailable" not in help_result.stderr
    run(interpreter, "-I", str(probe), "9999.0.1", "none")
    # Only the first fixture version exists yet, so the initial lock selects 0.1.0.
    environment["PYTHONPATH"] = str(pythonpath)
    environment["UV_NO_EDITABLE"] = "true"
    environment["UV_NO_SOURCES"] = "true"
    run(command, "plugins", "install", "ax-devil-smoke-plugin<1")
    run(command, "--help")
    environment.pop("PYTHONPATH")
    environment.pop("UV_NO_EDITABLE")
    environment.pop("UV_NO_SOURCES")
    root = installation_root(Host(base, "9999.0.1"))
    # installation_root uses the parent process's XDG_DATA_HOME, set by the plugin fixture.
    assert root.parent == tmp_path / "data/ax-devil"
    current = root / "current"
    prepared_python = str(current / ".venv/bin/python")
    run(prepared_python, "-I", str(probe), "9999.0.1", "0.1.0")
    app_requirement = read_project(current)["project"]["dependencies"][0]
    assert app_requirement == (f"ax-devil @ {app_wheel.as_uri()}" if installer == "wheel" else "ax-devil==9999.0.1")
    assert snapshot() == before

    (plugin / "pyproject.toml").write_text(plugin_metadata.replace('version = "0.1.0"', 'version = "0.2.0"'))
    build(plugin)
    run(command, "plugins", "update")
    run(prepared_python, "-I", str(probe), "9999.0.1", "0.1.0")
    run(command, "plugins", "update", "--upgrade")
    run(prepared_python, "-I", str(probe), "9999.0.1", "0.2.0")
    original = current.resolve()
    run(command, "plugins", "install", "ax-devil-smoke-plugin==99999", success=False)
    assert current.resolve() == original
    # Exercise actual validation rejection, not just a resolver conflict.
    plugin_code = plugin / "example_plugin.py"
    plugin_code.write_text(plugin_code.read_text().replace("return 1\n", "return 999\n"))
    run(command, "plugins", "install", str(plugin), success=False)
    assert current.resolve() == original
    assert snapshot() == before

    app_wheel = build_app("9999.0.2")
    if installer == "tool":
        run(sys.executable, "-I", "-m", "uv", "tool", "upgrade", "ax-devil")
    else:
        target = str(app_wheel) if installer == "wheel" else "ax-devil"
        run(interpreter, "-m", "pip", "install", "--upgrade", "--find-links", str(wheels), target)
    upgraded_base = snapshot()
    # The installed plugin rejects this app version, so the automatic refresh fails and the base app starts.
    stale = run(command, "--help")
    assert "updating plugins" in stale.stderr
    assert "Repair with: ax-devil plugins update" in stale.stderr
    assert "ax-devil-smoke-plugin" in run(command, "plugins", "list").stdout
    run(command, "plugins", "update", success=False)
    assert current.resolve() == original
    run(interpreter, "-I", str(probe), "9999.0.2", "none")
    # Publish a compatible fixture after verifying that an app upgrade can block refresh.
    plugin_code.write_text(plugin_code.read_text().replace("return 999\n", "return 1\n"))
    (plugin / "pyproject.toml").write_text(
        plugin_metadata.replace('version = "0.1.0"', 'version = "0.3.0"').replace("ax-devil<9999.0.2", "ax-devil")
    )
    build(plugin)
    refreshed = run(command, "--help")
    assert "updating plugins" in refreshed.stderr
    assert "Plugin installation unavailable" not in refreshed.stderr
    run(prepared_python, "-I", str(probe), "9999.0.2", "0.3.0")
    assert "updating plugins" not in run(command, "--help").stderr
    run(command, "plugins", "remove", "ax-devil-smoke-plugin")
    assert not current.exists()
    run(interpreter, "-I", str(probe), "9999.0.2", "none")
    wheel = next(wheels.glob("ax_devil_smoke_plugin-0.3.0-*.whl"))
    run(command, "plugins", "install", str(wheel))
    run(prepared_python, "-I", str(probe), "9999.0.2", "0.3.0")
    run(command, "plugins", "remove", "--all")
    assert not list(root.glob("project-*"))
    assert snapshot() == upgraded_base
    assert all((repository / name).read_bytes() == content for name, content in original_files.items())
