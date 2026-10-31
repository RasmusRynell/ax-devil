from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication

from ax_devil.modules.application_shell.about_dialog import AboutDialog
from ax_devil.modules.application_shell.configuration_preferences import save_overlay_preference
from ax_devil.modules.application_shell.restart import restart_application
from ax_devil.modules.catalog_viewer import close_catalog_viewer, show_catalog_viewer
from ax_devil.modules.chrome.choice_dialog import notify
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.diagnostics.debug_window import DebugWindow
from ax_devil.modules.diagnostics.metrics_store import metrics_enabled, set_metrics_enabled
from ax_devil.modules.plugin_manager.plugins_view import PLUGINS_VIEW_ID, PluginsView
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.video_player.orchestration.fullscreen import LaneFullscreenController
from ax_devil.modules.workspace.core import WorkspaceItem, workspace_file_stem
from ax_devil.modules.workspace.ui.session import WorkspaceSession

if TYPE_CHECKING:
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


APP_NAME = "ax-devil"

# The shortcuts that act on the focused viewer. The other ``acts_on_viewer`` shortcuts, Add Bookmark and lane
# fullscreen, are wired to the session and the window.
_VIEWER_COMMANDS: dict[str, Callable[[ViewerWidget], None]] = {
    "playback.play_pause": lambda viewer: viewer.toggle_playback(),
    "playback.step_forward": lambda viewer: viewer.step_frames(1),
    "playback.step_backward": lambda viewer: viewer.step_frames(-1),
    "playback.step_forward_10": lambda viewer: viewer.step_frames(10),
    "playback.step_backward_10": lambda viewer: viewer.step_frames(-10),
    "playback.speed_down": lambda viewer: viewer.decrease_playback_speed(),
    "playback.speed_up": lambda viewer: viewer.increase_playback_speed(),
    "navigation.next_entry": lambda viewer: viewer.step_next_entry(),
    "navigation.prev_entry": lambda viewer: viewer.step_prev_entry(),
    "view.toggle_info": lambda viewer: viewer.toggle_info_overlay(),
    "view.toggle_media_tools": lambda viewer: viewer.toggle_media_tools(),
    "view.zoom_in": lambda viewer: viewer.zoom(ZoomStep.IN),
    "view.zoom_out": lambda viewer: viewer.zoom(ZoomStep.OUT),
    "view.reset_zoom": lambda viewer: viewer.zoom(ZoomStep.RESET),
    "view.export_video": lambda viewer: viewer.export_video(),
}


def window_title(name: str, modified: bool) -> str:
    """Return the main window title for the workspace called *name*, marked when it has unsaved changes."""
    return f"{'● ' if modified else ''}{name} — {APP_NAME}"


class MainWindow(ChromeWindow):
    """Top-level window that hosts the entire application.

    Provides the main application window with File, View, Debug and Help menus. Contains a central widget that fills
    the available space where all future pages, layouts or widgets can be plugged in.

    A hidden, empty Quick child establishes Quick composition for the selected backend before the first show, so
    adding a viewer never replaces the raster native window; dialogs and menus stay on the normal widget path. Qt
    owns its lifetime; it loads no scene and renders no frames.
    """

    # The sidebar stack's minimum is the maximum over its pages and goes stale for hidden ones, so the shell keeps the
    # fixed floor instead of a content-driven minimum.
    fits_content_minimum = False

    _fullscreen_action: QAction | None = None  # Set once the shortcuts exist; window-state events can come earlier.

    def __init__(
        self,
        shortcut_manager: ShortcutManager,
        render_catalog_manager: SceneRenderCatalogManager,
        use_custom_frame: bool = False,
    ) -> None:
        # The sidebar shows the workspace name; the desktop's task switcher still gets it from the window title.
        super().__init__(
            use_custom_frame=use_custom_frame,
            show_custom_frame_border=False,
            remember_size=True,
            title_bar_title=APP_NAME,
        )

        self._composition_host = QQuickWidget(self)  # see the class docstring
        self._composition_host.hide()
        self._logger = get_logger(__name__)
        self._shortcut_manager = shortcut_manager
        self._render_catalog_manager = render_catalog_manager

        self._debug_window: DebugWindow | None = None
        self._about_dialog: AboutDialog | None = None
        self._workspace_session = WorkspaceSession(self, render_catalog_manager=self._render_catalog_manager)
        self._plugins_view = PluginsView()
        self._plugins_view.restart_requested.connect(lambda: restart_application(self))
        self._workspace_session.add_sidebar_view(self._plugins_view.sidebar_view())

        self._setup_window()
        self._shortcut_manager.install(self)
        self._workspace_session.set_shortcut_manager(self._shortcut_manager)
        self._setup_menu_bar()
        self._setup_central_widget()
        self._workspace_session.state_changed.connect(self._update_title)
        self._update_title()
        # Every way of quitting passes here, including quitting after an error without closing this window.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._workspace_session.lifecycle.keep_unsaved_changes)
        self._viewer_actions = [
            self._shortcut_manager.get_action(definition.action_id)
            for definition in self._shortcut_manager.definitions()
            if definition.acts_on_viewer
        ]
        self._lane_fullscreen = LaneFullscreenController(self, self._viewer_actions)
        self._connect_shortcut_actions()

        self._logger.debug("Main window initialization completed")

    def _on_overlay_action(self, preference: OverlayPreference, enabled: bool) -> None:
        try:
            save_overlay_preference(ConfigManager(), GlobalSettings(), preference, enabled)
        except (ValueError, OSError) as exc:
            self._logger.error(f"Could not save {preference.value}: {exc}")
            self._overlay_actions[preference].setChecked(not enabled)
            notify(self, "Settings", f"Could not save this preference: {exc}")

    def _on_overlay_preference_changed(self, preference: OverlayPreference, enabled: bool) -> None:
        self._overlay_actions[preference].setChecked(enabled)

    def _setup_window(self) -> None:
        """Configure basic window properties."""
        self.setObjectName("AxDevilMainWindow")
        self.setWindowTitle(APP_NAME)

    def _update_title(self) -> None:
        """Show the workspace name and whether it has unsaved changes in the title."""
        self.setWindowTitle(window_title(self._workspace_session.workspace_name, self._workspace_session.is_modified))

    def _setup_menu_bar(self) -> None:
        """Create and configure the menu bar with File, View, Debug, and Help menus."""
        menu_bar = self.menu_host()
        assert menu_bar is not None
        sm = self._shortcut_manager

        # File menu
        file_menu = menu_bar.addMenu("File")
        assert file_menu is not None

        session = self._workspace_session
        new_workspace_action = sm.get_action("app.new_workspace")
        new_workspace_action.triggered.connect(lambda: session.lifecycle.new_workspace())
        file_menu.addAction(new_workspace_action)

        open_workspace_action = sm.get_action("app.open_workspace")
        open_workspace_action.triggered.connect(lambda: session.lifecycle.open_workspace())
        file_menu.addAction(open_workspace_action)

        self._recent_menu = file_menu.addMenu("Open Recent")
        assert self._recent_menu is not None
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)

        save_workspace_action = sm.get_action("app.save_workspace")
        save_workspace_action.triggered.connect(lambda: session.lifecycle.save_workspace())
        file_menu.addAction(save_workspace_action)

        save_workspace_as_action = sm.get_action("app.save_workspace_as")
        save_workspace_as_action.triggered.connect(lambda: session.lifecycle.save_workspace_as())
        file_menu.addAction(save_workspace_as_action)

        file_menu.addSeparator()

        add_video_action = sm.get_action("app.add_video")
        add_video_action.triggered.connect(lambda: session.add_video())  # Not ``checked`` as the selection.
        file_menu.addAction(add_video_action)

        add_live_action = sm.get_action("app.add_live_stream")
        add_live_action.triggered.connect(lambda: session.add_live_stream())
        file_menu.addAction(add_live_action)

        add_playlist_action = sm.get_action("app.add_playlist")
        add_playlist_action.triggered.connect(lambda: session.add_playlist())  # Not ``checked`` as the resolver id.
        file_menu.addAction(add_playlist_action)

        file_menu.addSeparator()

        shortcuts_action = sm.get_action("app.keyboard_shortcuts")
        shortcuts_action.triggered.connect(self._on_keyboard_shortcuts)
        file_menu.addAction(shortcuts_action)

        settings_action = sm.get_action("app.settings")
        settings_action.triggered.connect(self._on_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        exit_action = sm.get_action("app.exit")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menu_bar.addMenu("View")
        assert view_menu is not None
        view_menu.addAction(sm.get_action("view.toggle_fullscreen"))
        view_menu.addAction(sm.get_action("view.toggle_lane_fullscreen"))
        view_menu.addAction(sm.get_action("view.toggle_media_tools"))
        view_menu.addAction(sm.get_action("view.toggle_sidebar"))
        view_menu.addSeparator()
        view_menu.addAction(sm.get_action("view.zoom_in"))
        view_menu.addAction(sm.get_action("view.zoom_out"))
        view_menu.addAction(sm.get_action("view.reset_zoom"))
        view_menu.addSeparator()
        settings = GlobalSettings()
        self._overlay_actions: dict[OverlayPreference, QAction] = {}
        for preference in OverlayPreference:
            action = QAction(preference.label, self)
            action.setCheckable(True)
            action.setChecked(settings.is_overlay_enabled(preference))
            action.setToolTip(preference.description)
            action.triggered.connect(
                lambda enabled, preference=preference: self._on_overlay_action(preference, enabled)
            )
            view_menu.addAction(action)
            self._overlay_actions[preference] = action
        settings.overlay_preference_changed.connect(self._on_overlay_preference_changed)
        view_menu.addAction(sm.get_action("view.toggle_info"))
        view_menu.addSeparator()
        view_menu.addAction(sm.get_action("view.add_bookmark"))
        view_menu.addAction(sm.get_action("view.export_video"))
        view_menu.addSeparator()
        render_catalogs_action = sm.get_action("view.render_catalogs")
        render_catalogs_action.setObjectName("renderCatalogsAction")
        render_catalogs_action.triggered.connect(self.show_catalog_viewer)
        view_menu.addAction(render_catalogs_action)

        # Debug menu
        debug_menu = menu_bar.addMenu("Debug")
        assert debug_menu is not None

        open_debug_action = sm.get_action("app.debug_metrics")
        open_debug_action.triggered.connect(self.show_debug_window)
        debug_menu.addAction(open_debug_action)

        plugins_action = sm.get_action("app.plugins")
        plugins_action.triggered.connect(self.show_plugins)
        debug_menu.addAction(plugins_action)

        debug_menu.addSeparator()

        self._metrics_action = QAction("Collect Debug Metrics", self)
        self._metrics_action.setCheckable(True)
        self._metrics_action.setChecked(metrics_enabled())
        self._metrics_action.toggled.connect(self._on_metrics_toggled)
        debug_menu.addAction(self._metrics_action)

        # Help menu
        help_menu = menu_bar.addMenu("Help")
        assert help_menu is not None

        quick_setup_action = sm.get_action("app.quick_setup")
        quick_setup_action.triggered.connect(self.show_quick_setup)
        help_menu.addAction(quick_setup_action)

        about_action = sm.get_action("app.about")
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _fill_recent_menu(self) -> None:
        """List the recent workspace files; choosing one opens it."""
        menu = self._recent_menu
        menu.clear()
        for path in (entry.path for entry in self._workspace_session.lifecycle.recent_workspaces()):
            action = menu.addAction(workspace_file_stem(path))
            action.setToolTip(str(path))
            action.triggered.connect(lambda _=False, path=path: self._workspace_session.lifecycle.open_workspace(path))
        if menu.isEmpty():
            menu.addAction("None").setEnabled(False)

    def _setup_central_widget(self) -> None:
        """Set up the central widget with the application workspace."""
        self.setCentralWidget(self._workspace_session.widget())

    def _connect_shortcut_actions(self) -> None:
        """Wire shortcut actions to the focused viewer, the session, and the window."""
        sm = self._shortcut_manager
        for action_id, command in _VIEWER_COMMANDS.items():
            sm.get_action(action_id).triggered.connect(
                lambda _=False, command=command: self._route_to_focused_widget(command)
            )
        sm.get_action("view.add_bookmark").triggered.connect(lambda: self._workspace_session.bookmark_focused_viewer())
        sm.get_action("view.toggle_lane_fullscreen").triggered.connect(self._lane_fullscreen.toggle)

        fullscreen_action = sm.get_action("view.toggle_fullscreen")
        fullscreen_action.setCheckable(True)
        fullscreen_action.setChecked(self.isFullScreen())
        fullscreen_action.triggered.connect(self._set_fullscreen)
        self._fullscreen_action = fullscreen_action

        sidebar = self._workspace_session.widget()
        sidebar_action = sm.get_action("view.toggle_sidebar")
        sidebar_action.setCheckable(True)
        sidebar_action.setChecked(sidebar.is_sidebar_shown())
        sidebar_action.triggered.connect(sidebar.toggle_sidebar)
        sidebar.sidebar_shown_changed.connect(sidebar_action.setChecked)

        self._workspace_session.focused_widget_changed.connect(self._enable_viewer_actions)
        self._enable_viewer_actions(self._workspace_session.focused_widget())

    def _enable_viewer_actions(self, widget: ViewerWidget | None) -> None:
        """Enable the actions that act on the focused viewer only while there is one."""
        for action in self._viewer_actions:
            action.setEnabled(widget is not None)

    def _set_fullscreen(self, fullscreen: bool) -> None:
        """Enter or leave fullscreen window mode."""
        if fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """Keep the Fullscreen menu check in step with the window, however fullscreen was entered or left."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self._fullscreen_action is not None:
            self._fullscreen_action.setChecked(self.isFullScreen())

    def _route_to_focused_widget(self, action: Callable[[ViewerWidget], None]) -> None:
        """Run *action* against the focused viewer widget when one exists."""
        widget = self._workspace_session.focused_widget()
        if widget is not None:
            action(widget)

    def _on_keyboard_shortcuts(self) -> None:
        """Open the keyboard shortcuts settings dialog."""
        from ax_devil.modules.shortcuts.shortcuts_dialog import ShortcutsDialog

        with ShortcutsDialog(self._shortcut_manager, parent=self) as dialog:
            dialog.exec()

    def _on_settings(self) -> None:
        """Open the global settings dialog."""
        from ax_devil.modules.application_shell.settings_dialog import SettingsDialog

        with SettingsDialog(parent=self, shortcut_manager=self._shortcut_manager) as dialog:
            dialog.exec()
            restart = dialog.restart_requested
        self._workspace_session.refresh_recent_workspaces()
        self._workspace_session.refresh_scan_defaults()
        if restart:
            restart_application(self)

    def show_catalog_viewer(self) -> None:
        """Show the render catalog viewer, as View → Render Catalogs does."""
        show_catalog_viewer(self._render_catalog_manager, parent=self)

    def launch(self, items: Sequence[WorkspaceItem] = (), workspace_file: Path | None = None) -> None:
        """Open what the command line asked for, if anything; see ``WorkspaceLifecycle.launch``."""
        self._workspace_session.scan_network_on_startup()
        self._workspace_session.lifecycle.launch(items, workspace_file)

    def show_quick_setup(self) -> None:
        """Show Quick Setup for theme and text size, as on first start and from Help → Quick Setup."""
        from ax_devil.modules.application_shell.quick_setup_dialog import QuickSetupDialog

        with QuickSetupDialog(parent=self) as dialog:
            dialog.exec()

    def _on_about(self) -> None:
        """Handle Help -> About action."""
        self._logger.debug("User triggered Help -> About")
        if self._about_dialog is not None and self._about_dialog.isVisible():
            self._about_dialog.raise_()
            self._about_dialog.activateWindow()
            return

        self._about_dialog = AboutDialog(parent=self)
        self._about_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._about_dialog.finished.connect(self._clear_about_dialog)
        self._about_dialog.show()

    def _clear_about_dialog(self) -> None:
        """Drop the cached About dialog reference after it closes."""
        self._about_dialog = None

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """Handle main window close event."""
        self._logger.info("Main window closing")

        self._lane_fullscreen.exit()
        self._workspace_session.cleanup()

        if self._debug_window is not None:
            self._debug_window.close()
            self._debug_window = None
        if self._about_dialog is not None:
            self._about_dialog.close()
            self._about_dialog = None
        close_catalog_viewer(self._render_catalog_manager)
        # Accept the close event
        if event:
            super().closeEvent(event)
        self._logger.debug("Main window closed")

    def show_debug_window(self) -> None:
        """Show the metrics debug window."""
        if self._debug_window is None:
            self._debug_window = DebugWindow(parent=self, use_custom_frame=self.uses_custom_frame)
            self._debug_window.destroyed.connect(self._on_debug_window_destroyed)

        self._debug_window.show()
        self._debug_window.raise_()
        self._debug_window.activateWindow()

    def _on_debug_window_destroyed(self, _obj: QObject | None = None) -> None:
        """Reset reference when the debug window is closed."""
        self._debug_window = None

    def show_plugins(self) -> None:
        """Show the Plugins view in the sidebar."""
        self._workspace_session.show_sidebar_view(PLUGINS_VIEW_ID)

    def _on_metrics_toggled(self, enabled: bool) -> None:
        """Turn debug metrics collection on or off."""
        set_metrics_enabled(enabled)
        self._logger.info(f"Debug metrics collection {'enabled' if enabled else 'disabled'}")
