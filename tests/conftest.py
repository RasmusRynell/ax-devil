"""conftest.py.

Shared test fixtures and configuration for all tests. The run's own home folder is set up in ``tests/__init__.py``;
the session hooks here fail the run if the user's real ax-devil folders changed anyway.
"""

import gc
import json
import shutil
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from ax_devil.app import _collect_due_generation
from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.plugin_system import ApplicationPluginLoader
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogManager,
    SceneRenderCatalogSelection,
    SceneRenderCatalogStore,
    create_scene_render_catalog_manager,
)
from ax_devil.modules.settings.config_manager import ConfigManager
from tests import OWNS_TEST_HOME, REAL_DATA_HOME, REAL_HOME, TEST_HOME
from tests.catalog_helpers import catalog_document
from tests.helpers.video import create_test_video

_USER_FOLDERS = (
    REAL_HOME / ".ax_devil" / "configs",
    REAL_HOME / ".ax_devil" / "render_catalogs",
    REAL_HOME / ".ax_devil" / "plugins",
    REAL_DATA_HOME / "ax-devil",
)
"""The user's own ax-devil data. Logs, caches and window state are left out: a running app changes them."""
_USER_FILES_BEFORE: dict[Path, tuple[int, int]] = {}


def _user_files() -> dict[Path, tuple[int, int]]:
    """Return the modification time and size of every entry in the user's folders, two levels deep."""
    found: dict[Path, tuple[int, int]] = {}
    for folder in _USER_FOLDERS:
        for pattern in ("*", "*/*"):
            for path in folder.glob(pattern):
                # lstat: a link is recorded as itself, so a broken one cannot stop the run.
                stat = path.lstat()
                found[path] = (stat.st_mtime_ns, stat.st_size)
    return found


def pytest_sessionstart(session: pytest.Session) -> None:
    """Remember the user's ax-devil folders as they are before any test runs."""
    _USER_FILES_BEFORE.update(_user_files())


def pytest_collection_finish(session: pytest.Session) -> None:
    """Freeze the collected tests out of later collections and turn automatic cyclic GC off, as the app does.

    Left on, a collection started by any allocation, including one inside a Qt callback such as an event filter or an
    icon engine's paint, can destroy widgets that earlier tests left in reference cycles while Qt is still iterating
    or painting them, and crash the run. The teardown hook collects instead. Under xdist this runs in each worker,
    not in the controller, which runs no tests.
    """
    gc.collect()
    gc.freeze()
    gc.disable()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run when anything in the user's ax-devil folders was created, changed or deleted.

    Only the process that created the home folder removes it; nested runs reuse it.
    """
    if OWNS_TEST_HOME:
        shutil.rmtree(TEST_HOME, ignore_errors=True)
    after = _user_files()
    changed = sorted(
        path for path in _USER_FILES_BEFORE.keys() | after.keys() if _USER_FILES_BEFORE.get(path) != after.get(path)
    )
    if not changed:
        return
    listed = "\n".join(f"  {path}" for path in changed)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    message = (
        f"Tests must not touch the user's files, but these changed during the run:\n{listed}\n"
        "(If ax-devil itself was running during the tests, it may have made these changes.)"
    )
    if reporter is not None:
        reporter.write_line(message, red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown() -> Generator[None, None, None]:
    """Finish pytest-qt's deferred widget deletions and collect garbage before the next test starts."""
    yield
    # pytest-qt closes widgets with deleteLater(); processEvents() alone does not
    # drain DeferredDelete events. Destroy them here, on the GUI thread.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # Automatic collection is off (see pytest_collection_finish). No Qt code is running here, so collect as the app's
    # timer does.
    _collect_due_generation()


@pytest.fixture(scope="session", autouse=True)
def _load_plugins(tmp_path_factory: pytest.TempPathFactory) -> Generator[Path, None, None]:
    """Point the app at an isolated test configuration and register built-in plugins, not installed ones.

    Yields the test configuration's path.
    """
    config_manager = ConfigManager()
    try:
        test_config_path = tmp_path_factory.mktemp("config") / "default.json"
        test_storage_root = tmp_path_factory.mktemp("storage")
        config_manager.set_config_path(test_config_path, create_if_missing=True)
        config_manager.reset()
        config_manager.set(
            "storage",
            {
                "base_dir": str(test_storage_root),
                "cache_dir": str(test_storage_root / "caches"),
                "logs_dir": str(test_storage_root / "logs"),
                "render_catalogs_dir": str(test_storage_root / "render_catalogs"),
            },
        )
        config_manager.save()

        with patch("ax_devil.modules.plugin_system.loader.importlib.metadata.entry_points", return_value=[]):
            ApplicationPluginLoader.load_all()
        yield test_config_path
    finally:
        ApplicationPluginLoader._loaded = False


@pytest.fixture(autouse=True)
def _restore_test_config(_load_plugins: Path) -> Generator[None, None, None]:
    """Point the app back at the test configuration after each test.

    CLI commands and settings tests switch the shared configuration to another file. Without this, later tests would
    use that file's storage folders instead of the ones this suite set up. Remembered window state is cleared too.
    """
    yield
    ConfigManager().set_config_path(_load_plugins, create_if_missing=False)
    # Remembered window sizes and panel states would otherwise carry over into the next test.
    (Path(ConfigManager().get("storage")["base_dir"]) / "window-state.ini").unlink(missing_ok=True)


@pytest.fixture(scope="session")
def small_catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Share immutable source bytes; each test still creates and validates its own catalog."""
    path = tmp_path_factory.mktemp("catalog-source") / "catalog.json"
    path.write_text(json.dumps(catalog_document()), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def test_temp_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a pytest-managed directory for shared generated media."""
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def video_file_factory(test_temp_root: Path) -> Callable[[float, int], Path]:
    """Reuse generated media bytes while callers own independent decoders and caches."""

    def video_file(duration: float, fps: int) -> Path:
        path = test_temp_root / f"test-{duration}-{fps}.mp4"
        if not path.exists():
            create_test_video(path, duration=duration, fps=fps)
        return path

    return video_file


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give file-provider tests an independent cache directory."""
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(CacheManager, "get_cache_subdir", lambda *_args, **_kwargs: cache)
    return cache


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Keep the existing fixture name for pytest-managed per-test directories."""
    return tmp_path


@pytest.fixture
def render_catalog_manager(temp_dir: Path) -> SceneRenderCatalogManager:
    """Return an initialized render catalog manager with isolated user catalog storage."""
    return create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(temp_dir / "render_catalogs"))


@pytest.fixture
def render_catalog_selection(render_catalog_manager: SceneRenderCatalogManager) -> SceneRenderCatalogSelection:
    """Return an independent render catalog selection backed by the shared test manager."""
    return render_catalog_manager.create_selection()


@pytest.fixture
def restore_app_appearance(qapp: QApplication) -> Iterator[None]:
    """Put back the application font, palette and stylesheet that a test changes, such as by changing the text size."""
    font, palette, stylesheet = qapp.font(), qapp.palette(), qapp.styleSheet()
    yield
    qapp.setFont(font)
    qapp.setPalette(palette)
    qapp.setStyleSheet(stylesheet)
