"""Tests for application version resolution."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from ax_devil import version as version_module


def test_get_app_version_prefers_installed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installed package metadata should be the primary version source."""
    monkeypatch.setattr(version_module, "metadata_version", lambda _: "1.2.3")
    monkeypatch.setattr(version_module, "_read_pyproject_version", lambda: "9.9.9")

    assert version_module.get_app_version() == "1.2.3"


def test_get_app_version_falls_back_to_pyproject(monkeypatch: pytest.MonkeyPatch) -> None:
    """Source checkouts should fall back to pyproject.toml."""

    def raise_package_not_found(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(version_module, "metadata_version", raise_package_not_found)
    monkeypatch.setattr(version_module, "_read_pyproject_version", lambda: "2.3.4")

    assert version_module.get_app_version() == "2.3.4"


def test_get_app_version_returns_unknown_when_no_version_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stable fallback avoids crashes in unusual environments."""

    def raise_package_not_found(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(version_module, "metadata_version", raise_package_not_found)
    monkeypatch.setattr(version_module, "_read_pyproject_version", lambda: None)

    assert version_module.get_app_version() == version_module.UNKNOWN_VERSION


def test_read_pyproject_version_reads_project_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pyproject fallback should read the version from the [project] section."""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """
[build-system]
requires = ["setuptools>=45"]

[project]
name = "ax-devil"
version = "3.4.5"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(version_module, "_find_pyproject_toml", lambda: pyproject_path)

    assert version_module._read_pyproject_version() == "3.4.5"
