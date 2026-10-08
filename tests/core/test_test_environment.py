"""Regression coverage for offscreen setup and nested test-home ownership."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.mark.parametrize(
    "nested,worker", [(False, None), (True, None), (True, "gw0")], ids=["owned-home", "parent-home", "worker-home"]
)
def test_subprocess_only_removes_its_own_home(tmp_path: Path, nested: bool, worker: str | None) -> None:
    """Collection clears desktop themes and preserves a home supplied by its parent."""
    shared_home = tmp_path / "parent-home"
    shared_home.mkdir()
    marker = shared_home / "parent-data"
    marker.write_text("keep", encoding="utf-8")
    home_path = tmp_path / "collected-home.txt"
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "wayland;xcb"
    environment["QT_QPA_PLATFORMTHEME"] = "gtk3"
    environment.pop("AX_DEVIL_TESTS_QPA_PLATFORM", None)
    environment.pop("AX_DEVIL_TESTS_HOME", None)
    environment.pop("AX_DEVIL_TESTS_HOME_WORKER", None)
    environment.pop("PYTEST_XDIST_WORKER", None)
    if worker is not None:
        environment["PYTEST_XDIST_WORKER"] = worker
    if nested:
        environment["AX_DEVIL_TESTS_HOME"] = str(shared_home)
    code = dedent(
        """\
        import os
        import sys
        from pathlib import Path

        import tests

        assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
        assert "QT_QPA_PLATFORMTHEME" not in os.environ
        Path(sys.argv[1]).write_text(str(tests.TEST_HOME), encoding="utf-8")
        import subprocess

        nested = subprocess.run(
            [sys.executable, "-c", "import tests; assert not tests.OWNS_TEST_HOME; "
             "assert str(tests.TEST_HOME) == __import__('os').environ['AX_DEVIL_TESTS_HOME']"],
            check=False,
        )
        assert nested.returncode == 0

        import pytest

        raise SystemExit(pytest.main(["--collect-only", "-q", "tests/core/test_version.py"]))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(home_path)],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    collected_home = Path(home_path.read_text(encoding="utf-8"))
    reuses_parent = nested and worker is None
    assert collected_home.exists() is reuses_parent
    assert marker.read_text(encoding="utf-8") == "keep"
    if reuses_parent:
        assert collected_home == shared_home
    else:
        assert collected_home != shared_home


def test_native_platform_requires_a_test_specific_override() -> None:
    """Private-display checks can explicitly select native Qt without inheriting the desktop platform."""
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "wayland;xcb"
    environment["AX_DEVIL_TESTS_QPA_PLATFORM"] = "xcb"
    result = subprocess.run(
        [sys.executable, "-c", 'import os; import tests; assert os.environ["QT_QPA_PLATFORM"] == "xcb"'],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
