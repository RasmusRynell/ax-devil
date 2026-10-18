"""Shared documented plugin package and uv stub for installation tests."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from uv import find_uv_bin

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
