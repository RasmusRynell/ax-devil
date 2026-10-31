"""CLI discovery stays independent of the graphical application."""

import subprocess
import sys


def test_cli_import_does_not_load_qt() -> None:
    """Discovery commands can register without paying the Qt startup cost."""
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import ax_devil.cli; import sys; assert not any(name.startswith('PySide6') for name in sys.modules)",
        ],
        check=True,
        capture_output=True,
    )
