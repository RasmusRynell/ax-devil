"""The Workspace core must stay usable without PySide6."""

from __future__ import annotations

import subprocess
import sys
import textwrap

_IMPORT_ALL_CORE_MODULES = textwrap.dedent(
    """
    import importlib
    import pkgutil
    import sys

    import ax_devil.modules.workspace.core as core

    for module in pkgutil.walk_packages(core.__path__, f"{core.__name__}."):
        importlib.import_module(module.name)

    loaded = sorted(name for name in sys.modules if name == "PySide6" or name.startswith("PySide6."))
    if loaded:
        print("\\n".join(loaded))
        sys.exit(1)
    """
)


def test_importing_every_core_module_loads_no_pyside6() -> None:
    """A fresh interpreter importing all of ``workspace.core`` never loads a PySide6 module."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_ALL_CORE_MODULES], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"PySide6 modules loaded by workspace.core:\n{result.stdout}{result.stderr}"
