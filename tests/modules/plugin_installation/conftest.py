"""Shared documented plugin package for installation tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ax_devil.modules.plugin_installation.host import current_host


@pytest.fixture
def plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use the minimal plugin package from the write-plugin skill."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    path = tmp_path / "example plugin"
    path.mkdir()
    guide = (current_host().location / ".agents/skills/write-plugin/reference.md").read_text()
    for filename, language in (("pyproject.toml", "toml"), ("example_plugin.py", "python")):
        block = re.search(rf"### {re.escape(filename)}\n\n```{language}\n(.*?)```", guide, re.DOTALL)
        assert block is not None
        (path / filename).write_text(block.group(1))
    return path
