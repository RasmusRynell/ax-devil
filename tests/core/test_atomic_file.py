"""A file written atomically is the old one or the complete new one."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ax_devil.core.atomic_file import write_atomically


def test_a_failed_write_keeps_the_old_file_and_a_finished_one_keeps_its_permissions(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "config.json"
    target.parent.mkdir()
    target.write_text("old")
    target.chmod(0o640)

    with pytest.raises(RuntimeError), write_atomically(target) as file:
        file.write("partial")
        raise RuntimeError("disk full")
    assert target.read_text() == "old"

    with write_atomically(target) as file:
        file.write("new")
    assert target.read_text() == "new"
    if os.name == "posix":  # Windows has no permission bits beyond read-only.
        assert target.stat().st_mode & 0o777 == 0o640
    assert list(target.parent.iterdir()) == [target]
