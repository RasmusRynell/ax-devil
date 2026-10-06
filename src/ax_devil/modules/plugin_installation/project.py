"""Prepare and activate a locked uv project without modifying the base app environment."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from uv import find_uv_bin

from .host import Host

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ENTRY_POINT_GROUPS = ("ax_devil.decoder_plugins", "ax_devil.playlist_resolver_plugins")


def read_project(directory: Path) -> dict[str, Any]:
    """Read standard Python project metadata."""
    with (directory / "pyproject.toml").open("rb") as file:
        project: dict[str, Any] = tomllib.load(file)
    return project


def installation_root(host: Host) -> Path:
    """Return stable storage for a checkout or base Python environment."""
    digest = hashlib.sha256(str(host.location).encode()).hexdigest()[:12]
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data.expanduser().resolve() / "ax-devil" / f"plugins-{digest}"


def selected_plugins(project: Path) -> dict[str, str]:
    """Read plugin selections independently of the disposable virtual environment."""
    if not project.exists():
        return {}
    try:
        metadata = read_project(project)
        sources = metadata["tool"]["uv"].get("sources", {})
        selected: dict[str, str] = {}
        for value in metadata["project"]["dependencies"]:
            name = canonicalize_name(Requirement(value).name)
            if name != "ax-devil":
                selected[name] = sources[name]["path"] if name in sources else value
        return selected
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid plugin project metadata: {project}") from exc


def selected_indexes(project: Path) -> dict[str, str]:
    """Read named package indexes from the uv project."""
    if not project.exists():
        return {}
    try:
        indexes = read_project(project).get("tool", {}).get("uv", {}).get("index", [])
        return {index["name"]: index["url"] for index in indexes}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid plugin project metadata: {project}") from exc


def plugin_name(path: Path) -> str:
    """Validate a local plugin package and return its normalized distribution name."""
    try:
        project = read_project(path)["project"]
        name = project["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name):
            raise ValueError(f"Invalid plugin distribution name in {path}")
        name = re.sub(r"[-_.]+", "-", name.lower())
        if name == "ax-devil" or not any(project.get("entry-points", {}).get(group) for group in ENTRY_POINT_GROUPS):
            raise ValueError(f"{path} must declare an ax-devil plugin entry point")
        return name
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid plugin package metadata: {path}") from exc


def plugin_spec(value: str) -> tuple[str, str]:
    """Normalize a package requirement, editable directory, or local wheel."""
    path = Path(value).expanduser()
    if path.is_dir():
        path = path.resolve()
        return plugin_name(path), str(path)
    if path.is_file() and path.suffix == ".whl":
        name, _version, _build, _tags = parse_wheel_filename(path.name)
        specification = f"{name} @ {path.resolve().as_uri()}"
    else:
        specification = value
    requirement = Requirement(specification)
    name = canonicalize_name(requirement.name)
    if name == "ax-devil" or requirement.marker is not None:
        raise ValueError("Select a plugin requirement without environment markers; upgrade ax-devil separately")
    return name, specification


class OutdatedPluginEnvironment(ValueError):
    """The plugin environment was prepared for a different app, Python, or dependency set."""


def runtime_python(host: Host) -> Path | None:
    """Locate a compatible prepared interpreter without installing anything."""
    current = installation_root(host) / "current"
    if not current.is_symlink():
        return None
    project = current.resolve(strict=True)
    metadata = read_project(project)
    if metadata.get("tool", {}).get("ax-devil", {}).get("host") != host.fingerprint:
        raise OutdatedPluginEnvironment(
            "The app, Python, or its dependencies changed; the plugin environment needs an update"
        )
    interpreter = project / ".venv/bin/python"
    if not interpreter.is_file():
        raise ValueError("The plugin environment is missing")
    for name, source in selected_plugins(project).items():
        if Path(source).is_absolute() and not (Path(source) / "pyproject.toml").is_file():
            raise ValueError(f"Plugin source is missing: {name} ({source})")
    return interpreter


def _write_project(project: Path, host: Host, plugins: dict[str, str], indexes: dict[str, str]) -> None:
    sources = {name: source for name, source in plugins.items() if Path(source).is_absolute()}
    if host.editable:
        sources["ax-devil"] = str(host.location)
    dependencies = [host.requirement, *(name if name in sources else value for name, value in plugins.items())]
    lines = [
        "[project]",
        'name = "ax-devil-plugins"',
        'version = "0.0.0"',
        f'requires-python = "=={sys.version_info.major}.{sys.version_info.minor}.*"',
        f"dependencies = {json.dumps(dependencies)}",
        "",
        "[tool.ax-devil]",
        f"host = {json.dumps(host.fingerprint)}",
        "",
        "[tool.uv]",
        f"environments = [\"sys_platform == '{sys.platform}'\"]",
        f"constraint-dependencies = {json.dumps(host.constraints)}",
        "",
        *(f"[[tool.uv.index]]\nname = {json.dumps(name)}\nurl = {json.dumps(url)}\n" for name, url in indexes.items()),
        "[tool.uv.sources]",
        *(f"{json.dumps(name)} = {{ path = {json.dumps(path)}, editable = true }}" for name, path in sources.items()),
    ]
    content = "\n".join(lines)
    (project / "pyproject.toml").write_text(f"{content}\n", encoding="utf-8")


def change_plugins(
    host: Host,
    *,
    install: tuple[str, ...] = (),
    remove: tuple[str, ...] = (),
    upgrade: bool = False,
    clear: bool = False,
    indexes: tuple[str, ...] = (),
    refresh: bool = False,
) -> None:
    """Resolve, validate, and atomically activate a changed plugin selection.

    A ``refresh`` waits for a concurrent installation and does nothing if that already prepared this app.
    """
    try:
        import fcntl
    except ImportError as exc:
        raise ValueError("Plugin installation requires Unix file locking") from exc

    root = installation_root(host)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX if refresh else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another plugin installation is running") from exc
        # Any explicit change is also the retry for a failed automatic refresh.
        (root / "failed").unlink(missing_ok=True)
        current = root / "current"
        if refresh:
            try:
                if runtime_python(host) is not None:
                    return
            except (OSError, ValueError):
                pass
        if clear:
            current.unlink(missing_ok=True)
            for directory in root.glob("project-*"):
                shutil.rmtree(directory)
            return
        plugins = selected_plugins(current)
        configured_indexes = selected_indexes(current)
        for index in indexes:
            try:
                name, url = index.split("=", 1)
            except ValueError as exc:
                raise ValueError(f"Invalid index {index!r}; expected NAME=URL") from exc
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or not url:
                raise ValueError(f"Invalid index {index!r}; expected NAME=URL")
            configured_indexes[name] = url
        for source in install:
            name, specification = plugin_spec(source)
            plugins[name] = specification
        for name in remove:
            normalized = canonicalize_name(name)
            if normalized not in plugins:
                raise ValueError(f"Plugin is not installed: {name}")
            del plugins[normalized]
        if not plugins:
            current.unlink(missing_ok=True)
            return
        project = Path(tempfile.mkdtemp(prefix="project-", dir=root))
        pending = root / "pending"
        try:
            _write_project(project, host, plugins, configured_indexes)
            if (current / "uv.lock").is_file():
                shutil.copyfile(current / "uv.lock", project / "uv.lock")
            # Explicit paths prevent an inherited uv environment from targeting the base .venv.
            environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(project / ".venv")}
            for key in (
                "VIRTUAL_ENV",
                "PYTHONPATH",
                "PYTHONHOME",
                "UV_FROZEN",
                "UV_LOCKED",
                "UV_NO_SYNC",
                "UV_NO_EDITABLE",
                "UV_NO_SOURCES",
                "UV_NO_SOURCES_PACKAGE",
            ):
                environment.pop(key, None)
            # Run the bundled binary directly: `python -m uv` would point uv at the base environment via VIRTUAL_ENV.
            command = [
                find_uv_bin(),
                "sync",
                "--project",
                str(project),
                "--python",
                sys.executable,
                "--no-default-groups",
            ]
            if upgrade:
                command.append("--upgrade")
            subprocess.run(command, env=environment, check=True)
            subprocess.run(
                [
                    str(project / ".venv/bin/python"),
                    "-I",
                    "-m",
                    "ax_devil.modules.plugin_installation.validate",
                    str(project),
                ],
                env={**environment, "QT_QPA_PLATFORM": "offscreen"},
                check=True,
            )
            previous = current.resolve() if current.is_symlink() else None
            pending.unlink(missing_ok=True)
            pending.symlink_to(project.name, target_is_directory=True)
            pending.replace(current)
        except BaseException:
            pending.unlink(missing_ok=True)
            shutil.rmtree(project)
            raise
        # Keep the replaced environment for processes still running it; older ones are unused.
        for directory in root.glob("project-*"):
            if directory not in (project, previous):
                shutil.rmtree(directory, ignore_errors=True)


def refresh_plugins(host: Host) -> None:
    """Rebuild the selection for a changed app; after a failure, wait for an explicit ``plugins update``."""
    failed = installation_root(host) / "failed"
    if failed.is_file() and failed.read_text() == host.fingerprint:
        raise ValueError("The automatic plugin update for this app version failed earlier")
    try:
        change_plugins(host, refresh=True)
    except (OSError, ValueError, subprocess.CalledProcessError):
        failed.write_text(host.fingerprint)
        raise
