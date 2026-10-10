"""Application factory for ax-devil."""

from __future__ import annotations

import gc
import sys
from collections.abc import Sequence
from logging import Logger
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.application_shell.restart import relaunch_if_requested
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme, setup_theme
from ax_devil.modules.diagnostics.exception_handler import install_exception_handler
from ax_devil.modules.plugin_system import ApplicationPluginLoader
from ax_devil.modules.scene.rendering import create_scene_render_catalog_manager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger, setup_logging
from ax_devil.modules.settings.paths import DEFAULT_CONFIG_PATH
from ax_devil.modules.settings.qt_logging import setup_qt_logging
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.workspace.core import WorkspaceItem
from ax_devil.version import APP_VERSION


class Application:
    """Create and run the Qt application."""

    def __init__(
        self,
        log_level: str = "INFO",
        config_path: Path | None = None,
        debug: bool = False,
        items: Sequence[WorkspaceItem] = (),
        workspace_file: Path | None = None,
        open_catalog_viewer: bool = False,
    ):
        """Store launch parameters: the Workspace Items to start a new workspace with, or the workspace file to open,
        and whether to show the catalog viewer on start. Without either, the kept workspace is restored."""
        self.log_level = log_level
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.debug = debug
        self.items = tuple(items)
        self.workspace_file = workspace_file
        self.open_catalog_viewer = open_catalog_viewer
        self.app: QApplication | None = None
        self.main_window: MainWindow | None = None
        self.logger: Logger | None = None

    def run(self) -> int:
        """Run the application and return the exit code."""
        try:
            setup_qt_logging()
            setup_logging(console_log_level=self.log_level, console_only=True)
            self.logger = get_logger(__name__)
            QApplication.setApplicationName("ax-devil")
            QApplication.setApplicationVersion(APP_VERSION)

            config_manager = ConfigManager()
            config_manager.set_config_path(self.config_path, create_if_missing=self.config_path == DEFAULT_CONFIG_PATH)
            config_manager.activate_storage()
            storage_settings = config_manager.get("storage", {}) or {}
            config_manager.ensure_storage_directories()
            logs_dir = str(storage_settings.get("logs_dir", ""))
            setup_logging(
                console_log_level=self.log_level,
                file_log_level=self.log_level,
                logs_dir=logs_dir,
            )
            ApplicationPluginLoader.load_all()

            self.app = QApplication(sys.argv)
            global_settings = GlobalSettings()
            global_settings.load_from_config(config_manager)
            self.use_custom_frame = global_settings.custom_frame
            # Qt caches this choice when the first widget window is created.
            global_settings.graphics_acceleration.configure()
            install_exception_handler(show_dialog=True)
            setup_theme(self.app, global_settings.theme.value, global_settings.text_size.body_px)
            global_settings.theme_changed.connect(apply_theme)
            global_settings.text_size_changed.connect(lambda size: apply_text_size(size.body_px))

            shortcut_overrides = config_manager.get("shortcuts", {}) or {}
            shortcut_manager = ShortcutManager(config_overrides=shortcut_overrides)
            shortcut_manager.register_defaults()

            render_catalog_manager = create_scene_render_catalog_manager()

            self.main_window = MainWindow(
                use_custom_frame=self.use_custom_frame,
                shortcut_manager=shortcut_manager,
                render_catalog_manager=render_catalog_manager,
            )
            self.main_window.show()
            self.main_window.launch(self.items, self.workspace_file)
            QTimer.singleShot(0, self._show_startup_windows)

            _collect_garbage_on_gui_thread(self.app, self.logger)

            if self.debug and self.main_window is not None:
                self.main_window.show_debug_window()

            exit_code = int(self.app.exec() if self.app else 1)

            overrides = shortcut_manager.get_config_overrides()
            if overrides != shortcut_overrides:
                config_manager.set("shortcuts", overrides)
                config_manager.save()

            if self.app is not None:
                relaunch_if_requested(self.app, self._global_options())
            return exit_code
        except Exception as exc:
            logger = self.logger or get_logger(__name__)
            logger.exception("Unhandled exception while running application")
            if self.debug:
                raise exc
            raise exc

    def _global_options(self) -> list[str]:
        """Return the command line options that apply to every launch, for a restarted process to keep."""
        options = ["--log-level", self.log_level, "--config", str(self.config_path)]
        return [*options, "--debug"] if self.debug else options

    def _show_startup_windows(self) -> None:
        """Open start-up windows in order; a modal dialog returns only when closed, so they never stack."""
        assert self.main_window is not None
        if not GlobalSettings().quick_setup_done:
            self.main_window.show_quick_setup()
        self.main_window.report_failed_plugins()
        if self.open_catalog_viewer:
            self.main_window.show_catalog_viewer()


def _collect_garbage_on_gui_thread(parent: QObject, logger: Logger) -> None:
    """Freeze the startup heap and replace automatic cyclic GC with a GUI-thread timer.

    Automatic collection runs on whichever thread allocates, so a worker thread could finalize Qt objects
    held in reference cycles and segfault. Freezing keeps startup objects out of every later scan, which
    removes the full-collection pauses that dropped playback frames; frozen objects that later become
    unreachable cycles are never freed.
    """
    gc.collect()
    gc.freeze()
    logger.info(f"Froze {gc.get_freeze_count()} startup objects out of garbage collection")
    gc.disable()
    timer = QTimer(parent)
    timer.timeout.connect(_collect_due_generation)
    timer.start(1000)


def _collect_due_generation() -> None:
    """Collect the oldest generation whose automatic threshold has been exceeded."""
    counts = gc.get_count()
    thresholds = gc.get_threshold()
    for generation in (2, 1, 0):
        if counts[generation] > thresholds[generation]:
            gc.collect(generation)
            return


def create_app(
    log_level: str = "INFO",
    config_path: Path | None = None,
    debug: bool = False,
    items: Sequence[WorkspaceItem] = (),
    workspace_file: Path | None = None,
    open_catalog_viewer: bool = False,
) -> Application:
    """Create application instance."""
    return Application(
        log_level=log_level,
        config_path=config_path,
        debug=debug,
        items=items,
        workspace_file=workspace_file,
        open_catalog_viewer=open_catalog_viewer,
    )
