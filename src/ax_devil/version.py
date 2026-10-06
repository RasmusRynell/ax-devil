"""Application version helpers."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from pathlib import Path

PACKAGE_NAME = "ax-devil"
UNKNOWN_VERSION = "0.0.0+unknown"


def _find_pyproject_toml() -> Path | None:
    """Locate the repository pyproject.toml when running from source."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _read_pyproject_version() -> str | None:
    """Read the project version from pyproject.toml without extra dependencies."""
    pyproject_path = _find_pyproject_toml()
    if pyproject_path is None:
        return None

    in_project_section = False
    version_pattern = re.compile(r'^version\s*=\s*"([^"]+)"')

    for line in pyproject_path.read_text(encoding="utf-8").splitlines():
        stripped_line = line.strip()

        if stripped_line == "[project]":
            in_project_section = True
            continue

        if in_project_section and stripped_line.startswith("["):
            break

        if not in_project_section:
            continue

        version_match = version_pattern.match(stripped_line)
        if version_match is not None:
            return version_match.group(1)

    return None


def get_app_version() -> str:
    """Return the installed package version, or a source-tree fallback."""
    try:
        return metadata_version(PACKAGE_NAME)
    except PackageNotFoundError:
        pyproject_version = _read_pyproject_version()
        if pyproject_version is not None:
            return pyproject_version
        return UNKNOWN_VERSION


APP_VERSION = get_app_version()
