"""Tests for application version resolution."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from ax_devil import version as version_module


def _not_installed(_: str) -> str:
    raise PackageNotFoundError


def test_installed_metadata_wins_over_source_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
    monkeypatch.setattr(version_module, "metadata_version", lambda _: "1.2.3")
    monkeypatch.setattr(version_module, "_find_pyproject_toml", lambda: pyproject)

    assert version_module.get_app_version() == "1.2.3"


def test_source_checkout_reads_project_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only the [project] table's version counts; build and tool tables may declare their own."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[build-system]\nrequires = ["setuptools>=45"]\n\n'
        '[project]\nname = "ax-devil"\nversion = "3.4.5"\n\n'
        '[tool.other]\nversion = "0.0.1"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(version_module, "metadata_version", _not_installed)
    monkeypatch.setattr(version_module, "_find_pyproject_toml", lambda: pyproject)

    assert version_module.get_app_version() == "3.4.5"


def test_unknown_version_when_neither_installed_nor_in_a_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stable fallback avoids crashes in unusual environments."""
    monkeypatch.setattr(version_module, "metadata_version", _not_installed)
    monkeypatch.setattr(version_module, "_find_pyproject_toml", lambda: None)

    assert version_module.get_app_version() == version_module.UNKNOWN_VERSION
