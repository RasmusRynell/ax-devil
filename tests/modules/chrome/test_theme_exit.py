"""Exiting after the OS-theme thread has ended must not terminate that thread."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = """
import sys

import qdarktheme._main as engine
from PySide6.QtWidgets import QApplication

from ax_devil.modules.chrome.theme import apply_theme

app = QApplication([])
apply_theme("auto")
listener = engine._listener
assert listener.wait(10000), "the OS-theme thread should end when gsettings is missing"
listener.terminate = lambda: sys.stdout.write("terminate called\\n")
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows watches the theme in the registry, not through gsettings")
def test_finished_os_theme_thread_is_not_terminated_at_exit(tmp_path: Path) -> None:
    """Terminating an ended thread signals a recycled native thread and crashes a normally closed app."""
    no_tools = tmp_path / "empty"
    no_tools.mkdir()

    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        env={
            "PATH": str(no_tools),
            "QT_QPA_PLATFORM": "offscreen",
            "HOME": str(tmp_path),
            # Windows cannot start Python without its system folder, nor find a home without USERPROFILE.
            **(
                {"SYSTEMROOT": os.environ["SYSTEMROOT"], "USERPROFILE": str(tmp_path)}
                if sys.platform == "win32"
                else {}
            ),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "terminate called" not in result.stdout
