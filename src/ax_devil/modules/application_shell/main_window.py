from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QLabel, QMessageBox

from ax_devil.modules.application_shell.about_dialog import AboutDialog
from ax_devil.modules.application_shell.configuration_preferences import save_overlay_preference
from ax_devil.modules.application_shell.restart import restart_application
from ax_devil.modules.catalog_viewer import close_catalog_viewer, show_catalog_viewer
from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.diagnostics.debug_window import DebugWindow
from ax_devil.modules.diagnostics.metrics_store import metrics_enabled, set_metrics_enabled
from ax_devil.modules.diagnostics.plugin_window import PluginWindow
from ax_devil.modules.plugin_system import PluginStatus, RuntimePluginRegistry
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.video_player.orchestration.fullscreen import LaneFullscreenController
from ax_devil.modules.workspace import StartupContent
from ax_devil.modules.workspace.session import WorkspaceSession

if TYPE_CHECKING:
    from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
    from ax_devil.modules.workspace.viewer_host import WorkspaceWidget


class MainWindow(ChromeWindow):
    """Top-level window that hosts the entire application.

    Provides the main application window with File, View, Debug and Help menus. Contains a central widget that fills
    the available space where all future pages, layouts or widgets can be plugged in.
    """

    def __init__(
        self,
        shortcut_manager: ShortcutManager,
        render_catalog_manager: SceneRenderCatalogManager,
        use_custom_frame: bool = False,
    ) -> None:
        super().__init__(use_custom_frame=use_custom_frame, show_custom_frame_border=False, remember_size=True)

        self._logger = get_logger(__name__)
        self._shortcut_manager = shortcut_manager
        self._render_catalog_manager = render_catalog_manager

        self._debug_window: DebugWindow | None = None
        self._plugin_window: PluginWindow | None = None
        self._about_dialog: AboutDialog | None = None
        self._workspace_session = WorkspaceSession(self, render_catalog_manager=self._render_catalog_manager)

        self._setup_window()
        self._shortcut_manager.install(self)
        self._workspace_session.set_welcome_shortcut_manager(self._shortcut_manager)
        self._setup_menu_bar()
        self._setup_central_widget()
        self._lane_fullscreen = LaneFullscreenController(
            self,
            [
                self._shortcut_manager.get_action(definition.action_id)
                for definition in self._shortcut_manager.definitions()
                if definition.acts_on_viewer
            ],
        )
        self._connect_shortcut_actions()

        self._logger.debug("Main window initialization completed")

    def _on_overlay_action(self, preference: OverlayPreference, enabled: bool) -> None:
        try:
            save_overlay_preference(ConfigManager(), GlobalSettings(), preference, enabled)
        except (ValueError, OSError) as exc:
            self._logger.error(f"Could not save {preference.value}: {exc}")
            self._overlay_actions[preference].setChecked(not enabled)
            QMessageBox.warning(self, "Settings", f"Could not save this preference: {exc}")

    def _on_overlay_preference_changed(self, preference: OverlayPreference, enabled: bool) -> None:
        self._overlay_actions[preference].setChecked(enabled)

    def _setup_window(self) -> None:
        """Configure basic window properties."""
        self.setObjectName("AxDevilMainWindow")
        self.setWindowTitle("ax-devil")

    def _setup_menu_bar(self) -> None:
        """Create and configure the menu bar with File, View, Debug, and Help menus."""
        menu_bar = self.menu_host()
        assert menu_bar is not None
        sm = self._shortcut_manager

        # File menu
        file_menu = menu_bar.addMenu("File")
        assert file_menu is not None

        add_video_action = sm.get_action("app.add_video")
        add_video_action.triggered.connect(self._on_add_video)
        file_menu.addAction(add_video_action)

        add_live_action = sm.get_action("app.add_live_stream")
        add_live_action.triggered.connect(self._on_add_live_stream)
        file_menu.addAction(add_live_action)

        add_playlist_action = sm.get_action("app.add_playlist")
        add_playlist_action.triggered.connect(self._on_add_playlist)
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

        open_plugin_action = QAction("Open Plug-ins Window", self)
        open_plugin_action.triggered.connect(self.show_plugin_window)
        debug_menu.addAction(open_plugin_action)

        debug_menu.addSeparator()

        self._metrics_action = QAction("Collect Debug Metrics", self)
        self._metrics_action.setCheckable(True)
        self._metrics_action.setChecked(metrics_enabled())
        self._metrics_action.toggled.connect(self._on_metrics_toggled)
        debug_menu.addAction(self._metrics_action)

        # Help menu
        help_menu = menu_bar.addMenu("Help")
        assert help_menu is not None

        quick_setup_action = QAction("Quick Setup", self)
        quick_setup_action.triggered.connect(self.show_quick_setup)
        help_menu.addAction(quick_setup_action)

        about_action = QAction("About", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _setup_central_widget(self) -> None:
        """Set up the central widget with the application workspace."""
        self.setCentralWidget(self._workspace_session.widget())

    def _connect_shortcut_actions(self) -> None:
        """Wire shortcut actions to viewer routing methods."""
        sm = self._shortcut_manager

        sm.get_action("playback.play_pause").triggered.connect(
            lambda: self._route_to_focused_widget(lambda widget: widget.toggle_playback())
        )
        sm.get_action("playback.step_forward").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.step_frames(1))
        )
        sm.get_action("playback.step_backward").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.step_frames(-1))
        )
        sm.get_action("playback.step_forward_10").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.step_frames(10))
        )
        sm.get_action("playback.step_backward_10").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.step_frames(-10))
        )
        sm.get_action("playback.speed_down").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.decrease_playback_speed())
        )
        sm.get_action("playback.speed_up").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.increase_playback_speed())
        )
        sm.get_action("navigation.next_entry").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.step_next_entry())
        )
        sm.get_action("navigation.prev_entry").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.step_prev_entry())
        )
        sm.get_action("view.toggle_info").triggered.connect(
            lambda: self._route_to_offline_viewer(lambda viewer: viewer.toggle_info_overlay())
        )
        sm.get_action("view.toggle_media_tools").triggered.connect(
            lambda: self._route_to_focused_widget(lambda widget: widget.toggle_media_tools())
        )
        sm.get_action("view.toggle_fullscreen").triggered.connect(self._toggle_fullscreen)
        sm.get_action("view.toggle_sidebar").triggered.connect(self._workspace_session.widget().toggle_sidebar)
        for action_id, step in (
            ("view.zoom_in", ZoomStep.IN),
            ("view.zoom_out", ZoomStep.OUT),
            ("view.reset_zoom", ZoomStep.RESET),
        ):
            sm.get_action(action_id).triggered.connect(
                lambda _=False, step=step: self._route_to_focused_widget(lambda widget: widget.zoom(step))
            )
        sm.get_action("view.toggle_lane_fullscreen").triggered.connect(self._lane_fullscreen.toggle)

    def _toggle_fullscreen(self) -> None:
        """Toggle between fullscreen and normal window mode."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _route_to_focused_widget(self, action: Callable[["WorkspaceWidget"], None]) -> None:
        """Run *action* against the focused workspace widget when one exists."""
        widget = self._workspace_session.focused_widget()
        if widget is not None:
            action(widget)

    def _route_to_offline_viewer(self, action: Callable[["OfflineVideoViewerWidget"], None]) -> None:
        """Run *action* against the focused offline viewer when one exists."""
        viewer = self._workspace_session.focused_offline_viewer()
        if viewer is not None:
            action(viewer)

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
        if restart:
            restart_application(self)

    def _on_add_video(self) -> None:
        """Handle File -> Add Video action."""
        from ax_devil.modules.workspace.add_content.add_video_dialog import AddVideoDialog

        with AddVideoDialog(self) as dialog:
            if dialog.exec() == AddVideoDialog.DialogCode.Accepted:
                result = dialog.get_result()
                if result is not None:
                    self._workspace_session.open_video(result)

    def _on_add_live_stream(self) -> None:
        """Handle File -> Add Live Stream action."""
        from ax_devil.modules.workspace.add_content.add_live_stream_dialog import AddLiveStreamDialog

        with AddLiveStreamDialog(self) as dialog:
            if dialog.exec() == AddLiveStreamDialog.DialogCode.Accepted:
                result = dialog.get_result()
                if result is not None:
                    self._workspace_session.add_content(result)
                    self._logger.info(f"Added live stream: {result.display_name}")

    def _on_add_playlist(self) -> None:
        """Handle File -> Add Playlist action."""
        from ax_devil.modules.workspace.add_content.add_playlist_dialog import AddPlaylistDialog

        with AddPlaylistDialog(self) as dialog:
            if dialog.exec() == AddPlaylistDialog.DialogCode.Accepted:
                results = dialog.get_result()
                if results:
                    self._workspace_session.add_contents(results)
                    self._logger.info(f"Added {len(results)} playlist(s)")

    def show_catalog_viewer(self) -> None:
        """Show the render catalog viewer, as View → Render Catalogs does."""
        show_catalog_viewer(self._render_catalog_manager, parent=self)

    def load_startup_content(self, startup: StartupContent) -> None:
        """Load resolved startup content into the workspace."""
        self._workspace_session.load_startup_content(startup)

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
        if self._plugin_window is not None:
            self._plugin_window.close()
            self._plugin_window = None
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

    def show_plugin_window(self) -> None:
        """Show the plug-in inspection window."""
        if self._plugin_window is None:
            self._plugin_window = PluginWindow(parent=self, use_custom_frame=self.uses_custom_frame)
            self._plugin_window.destroyed.connect(self._on_plugin_window_destroyed)

        self._plugin_window.show()
        self._plugin_window.raise_()
        self._plugin_window.activateWindow()

    def _on_plugin_window_destroyed(self, _obj: QObject | None = None) -> None:
        """Reset reference when the plug-in window is closed."""
        self._plugin_window = None

    def report_failed_plugins(self) -> None:
        """Show one dialog listing plug-ins that failed to load, if any."""
        failed = [record for record in RuntimePluginRegistry.list_plugins() if record.status is PluginStatus.FAILED]
        if not failed:
            return

        with BaseDialog(parent=self, title="Plug-ins failed to load") as dialog:
            label = QLabel("\n".join(f"{record.definition.plugin_id}: {record.error}" for record in failed))
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            dialog.add_content_widget(label)

            def open_plugin_window() -> None:
                dialog.accept()
                self.show_plugin_window()

            dialog.add_button("Open Plug-ins Window", open_plugin_window)
            dialog.add_standard_buttons(include_cancel=False)
            dialog.exec()

    def _on_metrics_toggled(self, enabled: bool) -> None:
        """Turn debug metrics collection on or off."""
        set_metrics_enabled(enabled)
        self._logger.info(f"Debug metrics collection {'enabled' if enabled else 'disabled'}")
