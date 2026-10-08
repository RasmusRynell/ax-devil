"""The test suite runs in its own home folder, so no test can read or change the user's files.

Everything ax-devil stores (configuration, render catalogs, window state, logs, caches and installed plugins) lives
under the home folder or an XDG folder, and the paths are computed when the app's modules are imported. This package is
imported before ``conftest.py`` and every test module, so pointing those variables at a temporary folder here moves
all of it out of the user's way. ``REAL_HOME`` keeps the user's home for the check in ``conftest.py`` that fails the run
if the user's ax-devil folders change anyway.

uv's download cache is shared data, not the user's settings, so it keeps its usual place to avoid downloading again.
A nested test run, such as a test starting pytest in a subprocess, reuses the outer run's folders.
"""

import os
import tempfile
from pathlib import Path

REAL_HOME = Path(os.environ.setdefault("AX_DEVIL_TESTS_REAL_HOME", str(Path.home())))
REAL_DATA_HOME = Path(
    os.environ.setdefault(
        "AX_DEVIL_TESTS_REAL_DATA_HOME", os.environ.get("XDG_DATA_HOME", str(REAL_HOME / ".local" / "share"))
    )
)
OWNS_TEST_HOME = "AX_DEVIL_TESTS_HOME" not in os.environ
# xdist workers inherit the controller's environment, but must not share mutable app storage.
# Subprocesses started inside a worker keep that worker's home, just like nested serial runs.
_worker = os.environ.get("PYTEST_XDIST_WORKER")
if _worker is not None and os.environ.get("AX_DEVIL_TESTS_HOME_WORKER") != _worker:
    OWNS_TEST_HOME = True
    os.environ["AX_DEVIL_TESTS_HOME_WORKER"] = _worker
if OWNS_TEST_HOME:
    os.environ["AX_DEVIL_TESTS_HOME"] = tempfile.mkdtemp(prefix="ax-devil-test-home-")
TEST_HOME = Path(os.environ["AX_DEVIL_TESTS_HOME"])

os.environ["QT_QPA_PLATFORM"] = os.environ.get("AX_DEVIL_TESTS_QPA_PLATFORM", "offscreen")
if os.environ["QT_QPA_PLATFORM"] == "offscreen":
    # Desktop themes can initialize GTK and try to open the user's display even with offscreen Qt.
    os.environ.pop("QT_QPA_PLATFORMTHEME", None)

os.environ.setdefault("UV_CACHE_DIR", str(Path(os.environ.get("XDG_CACHE_HOME", REAL_HOME / ".cache")) / "uv"))
for _variable in ("HOME", "USERPROFILE"):
    os.environ[_variable] = str(TEST_HOME)
for _variable, _folder in (
    ("XDG_CONFIG_HOME", ".config"),
    ("XDG_DATA_HOME", ".local/share"),
    ("XDG_CACHE_HOME", ".cache"),
    ("XDG_STATE_HOME", ".local/state"),
):
    os.environ[_variable] = str(TEST_HOME / _folder)
