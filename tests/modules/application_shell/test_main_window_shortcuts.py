from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMenu, QPushButton
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.catalog_viewer import CatalogViewerWindow, close_catalog_viewer
from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.workspace.core import (
    ItemResolution,
    LiveStreamItem,
    RecentWorkspaces,
    VideoItem,
    load_workspace,
)
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.live_view import LiveView
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.shortcuts import make_shortcut_manager
from tests.helpers.workspace import DummyViewer, FakePrompts


class _FocusedWidget:
    """Focused viewer widget test double."""

    def __init__(self) -> None:
        self.toggle_count = 0
        self.media_tools_toggle_count = 0
        self.zoom_steps: list[ZoomStep] = []

    def zoom(self, step: ZoomStep) -> None:
        """Record a keyboard zoom step."""
        self.zoom_steps.append(step)

    def toggle_media_tools(self) -> None:
        """Record a media tools toggle."""
        self.media_tools_toggle_count += 1

    def toggle_playback(self) -> None:
        """Record a playback toggle."""
        self.toggle_count += 1


class _OfflineViewer:
    """Offline viewer test double."""

    def __init__(self) -> None:
        self.step_deltas: list[int] = []
        self.speed_up_count = 0

    def step_frames(self, delta: int) -> None:
        """Record frame-step routing."""
        self.step_deltas.append(delta)

    def increase_playback_speed(self) -> None:
        """Record speed-up routing."""
        self.speed_up_count += 1


class _RoutingSession:
    """Workspace session test double for shortcut routing."""

    def __init__(self, focused_widget: _FocusedWidget | None, offline_viewer: _OfflineViewer | None) -> None:
        self._focused_widget = focused_widget
        self._offline_viewer = offline_viewer
        self.bookmark_requests = 0

    def focused_widget(self) -> _FocusedWidget | None:
        """Return the focused viewer widget."""
        return self._focused_widget

    def focused_offline_viewer(self) -> _OfflineViewer | None:
        """Return the focused offline viewer."""
        return self._offline_viewer

    def bookmark_focused_viewer(self) -> None:
        """Record a request to bookmark the focused viewer's frame."""
        self.bookmark_requests += 1

    def cleanup(self) -> None:
        """Match the workspace session teardown API."""


def test_play_pause_shortcut_routes_to_focused_workspace_widget(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    manager = make_shortcut_manager()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    focused_widget = _FocusedWidget()
    window._workspace_session = cast(WorkspaceSession, _RoutingSession(focused_widget, None))

    manager.get_action("playback.play_pause").trigger()

    assert focused_widget.toggle_count == 1


def test_offline_shortcuts_route_to_focused_offline_viewer(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    manager = make_shortcut_manager()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    focused_widget = _FocusedWidget()
    offline_viewer = _OfflineViewer()
    window._workspace_session = cast(WorkspaceSession, _RoutingSession(focused_widget, offline_viewer))

    manager.get_action("playback.step_forward").trigger()
    manager.get_action("playback.speed_up").trigger()

    assert focused_widget.toggle_count == 0
    assert offline_viewer.step_deltas == [1]
    assert offline_viewer.speed_up_count == 1


def test_media_tools_action_is_in_view_menu_and_routes_to_focused_widget(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    manager = make_shortcut_manager()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    focused_widget = _FocusedWidget()
    window._workspace_session = cast(WorkspaceSession, _RoutingSession(focused_widget, None))
    action = manager.get_action("view.toggle_media_tools")
    menus = [menu.title() for menu in action.associatedObjects() if isinstance(menu, QMenu)]

    assert menus == ["View"]
    assert action.shortcut().toString() == "Ctrl+B"

    action.trigger()

    assert focused_widget.media_tools_toggle_count == 1


def test_add_bookmark_is_in_view_menu_on_m_and_bookmarks_the_focused_viewer(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    manager = make_shortcut_manager()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    session = _RoutingSession(_FocusedWidget(), None)
    window._workspace_session = cast(WorkspaceSession, session)
    action = manager.get_action("view.add_bookmark")

    assert [menu.title() for menu in action.associatedObjects() if isinstance(menu, QMenu)] == ["View"]
    assert action.shortcut().toString() == "M"

    action.trigger()

    assert session.bookmark_requests == 1


def test_render_catalogs_action_is_in_view_menu_and_opens_the_catalog_viewer(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    manager = make_shortcut_manager()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    action = manager.get_action("view.render_catalogs")

    assert [menu.title() for menu in action.associatedObjects() if isinstance(menu, QMenu)] == ["View"]
    assert action.shortcut().toString() == "Ctrl+R"

    action.trigger()

    viewer = window.findChild(CatalogViewerWindow)
    assert viewer is not None and viewer.isVisible()
    close_catalog_viewer(render_catalog_manager)


def test_zoom_actions_are_in_view_menu_and_route_to_focused_widget(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    manager = make_shortcut_manager()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    focused_widget = _FocusedWidget()
    window._workspace_session = cast(WorkspaceSession, _RoutingSession(focused_widget, None))
    expected = {
        "view.zoom_in": ("Ctrl++", ZoomStep.IN),
        "view.zoom_out": ("Ctrl+-", ZoomStep.OUT),
        "view.reset_zoom": ("Ctrl+0", ZoomStep.RESET),
    }

    for action_id, (key, _step) in expected.items():
        action = manager.get_action(action_id)
        assert [menu.title() for menu in action.associatedObjects() if isinstance(menu, QMenu)] == ["View"]
        assert action.shortcut().toString() == key
        action.trigger()

    assert focused_widget.zoom_steps == [step for _key, step in expected.values()]


def test_view_display_preferences_save_and_follow_settings_changes(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    window = MainWindow(shortcut_manager=make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    settings = GlobalSettings()
    previous = settings.snapshot()
    view_menu = next(menu for menu in window.findChildren(QMenu) if menu.title() == "View")
    actions = {action.text(): action for action in view_menu.actions()}
    try:
        for preference in OverlayPreference:
            action = actions[preference.label]
            assert action.isChecked()
            action.trigger()
            assert not settings.is_overlay_enabled(preference)
            assert not ConfigManager().get("settings")["overlay_interaction"][preference.value]
        settings.apply_snapshot(previous)
        assert all(actions[preference.label].isChecked() for preference in OverlayPreference)
    finally:
        settings.apply_snapshot(previous)


@pytest.mark.parametrize("use_custom_frame", [False, True])
def test_workspace_actions_are_in_the_file_menu_and_the_title_shows_the_workspace_and_unsaved_changes(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path, use_custom_frame: bool
) -> None:
    manager = make_shortcut_manager()
    window = MainWindow(
        shortcut_manager=manager, render_catalog_manager=render_catalog_manager, use_custom_frame=use_custom_frame
    )
    qtbot.addWidget(window)
    session = window._workspace_session
    session.lifecycle._prompts = FakePrompts(save_path=tmp_path / "lot")
    expected_keys = {
        "app.new_workspace": "",
        "app.open_workspace": "Ctrl+O",
        "app.save_workspace": "Ctrl+S",
        "app.save_workspace_as": "Ctrl+Shift+S",
    }
    for action_id, key in expected_keys.items():
        action = manager.get_action(action_id)
        assert [menu.title() for menu in action.associatedObjects() if isinstance(menu, QMenu)] == ["File"]
        assert action.shortcut().toString() == key

    def assert_titles(name: str, modified: bool) -> None:
        assert window.windowTitle() == f"{'● ' if modified else ''}{name} — ax-devil"
        if use_custom_frame:
            title_label = window.findChild(QLabel, "AxDevilTitleLabel")
            assert title_label is not None
            assert title_label.text() == "ax-devil"
        view = window._workspace_session._workspace_view
        marks = [
            label.text()
            for label in view.findChildren(QLabel)
            if label.toolTip() == "Unsaved changes" and not label.isHidden()
        ]
        assert view.title() == name
        assert marks == (["●"] if modified else [])

    assert_titles("Untitled", modified=False)
    video = tmp_path / "lot.mp4"
    video.write_bytes(b"")
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        session.add_items([VideoItem(video=video)])
        qtbot.waitUntil(lambda: session.focused_widget() is not None)  # Items resolve in the background.
    assert_titles("Untitled", modified=True)
    session._workspace_view.title_edited.emit("Parking lot")
    assert_titles("Parking lot", modified=True)

    manager.get_action("app.save_workspace").trigger()

    assert_titles("Parking lot", modified=False)


def test_add_manually_in_the_live_view_opens_the_add_live_stream_dialog(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow(shortcut_manager=make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    opened: list[str] = []

    def cancel(dialog: BaseDialog) -> int:
        opened.append(dialog.windowTitle())
        return BaseDialog.DialogCode.Rejected

    monkeypatch.setattr(BaseDialog, "exec", cancel)
    window._workspace_session.show_sidebar_view("live")
    view = window.findChild(LiveView)
    assert view is not None
    [button] = [button for button in view.findChildren(QPushButton) if button.text() == "Add manually"]

    button.click()

    assert opened == ["Add Live Stream"]


@pytest.mark.parametrize("from_file_menu", [True, False])
def test_add_live_stream_starts_the_stream_only_from_the_file_menu_not_from_add_manually(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    monkeypatch: pytest.MonkeyPatch,
    from_file_menu: bool,
) -> None:
    manager = make_shortcut_manager()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    camera = ItemResolution.of(
        LiveStreamItem(host="192.0.2.10", label="Entrance"), default_resolution_context(), CancelToken()
    )
    monkeypatch.setattr(BaseDialog, "exec", lambda dialog: BaseDialog.DialogCode.Accepted)
    monkeypatch.setattr(AddLiveStreamDialog, "get_result", lambda dialog: camera)
    session = window._workspace_session
    session.show_sidebar_view("live")

    with patch("ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget", DummyViewer):
        if from_file_menu:
            manager.get_action("app.add_live_stream").trigger()
        else:
            view = window.findChild(LiveView)
            assert view is not None
            [button] = [button for button in view.findChildren(QPushButton) if button.text() == "Add manually"]
            button.click()

    assert [item.display_name for item in session._workspace_store.workspace.items] == ["Entrance"]
    assert (session.focused_widget() is not None) == from_file_menu


def test_quitting_keeps_unsaved_edits_even_without_closing_the_window(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    """Quitting after an unexpected error skips closing the window; unsaved edits still become a recent workspace."""
    window = MainWindow(shortcut_manager=make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    session = window._workspace_session
    session.lifecycle._unsaved_folder = lambda: tmp_path / "unsaved"
    session.lifecycle._recent_workspaces = RecentWorkspaces(lambda: tmp_path / "recent.json")
    video = tmp_path / "lot.mp4"
    video.write_bytes(b"")
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        session.add_items([VideoItem(video=video)])
        qtbot.waitUntil(lambda: session.focused_widget() is not None)  # Items resolve in the background.

    app = QApplication.instance()
    assert app is not None
    app.aboutToQuit.emit()

    [recent] = session.lifecycle.recent_workspaces()
    assert recent.path.parent == tmp_path / "unsaved"
    assert load_workspace(recent.path).items == session._workspace_store.workspace.items


def test_changing_storage_in_settings_refreshes_the_start_panels_recent_workspaces(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    """Deferring restart keeps the start panel and File menu on the same saved history."""
    config = ConfigManager()
    old_folder, new_folder = tmp_path / "old", tmp_path / "new"
    old_file, new_file = tmp_path / "Old.ax-devil.workspace", tmp_path / "New.ax-devil.workspace"
    RecentWorkspaces(lambda: old_folder / "recent-workspaces.json").record(old_file)
    RecentWorkspaces(lambda: new_folder / "recent-workspaces.json").record(new_file)
    storage = config.get_saved("storage")
    storage["base_dir"] = str(old_folder)
    config.set("storage", storage)
    config.activate_storage()
    manager = make_shortcut_manager()
    window = MainWindow(shortcut_manager=manager, render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    session = window._workspace_session
    assert [row.path for row in session._start_panel.recent_rows()] == [old_file]

    def save_settings(dialog: SettingsDialog) -> int:
        for field, editor in dialog._configuration_editors.items():
            if field.path == "storage.base_dir":
                edit = editor.findChild(QLineEdit)
                assert edit is not None
                edit.setText(str(new_folder))
        dialog._on_ok()
        return int(dialog.result())

    with (
        patch.object(SettingsDialog, "exec", save_settings),
        patch.object(SettingsDialog, "_ask_to_restart", return_value=False),
    ):
        manager.get_action("app.settings").trigger()

    assert [entry.path for entry in session.lifecycle.recent_workspaces()] == [new_file]
    assert [row.path for row in session._start_panel.recent_rows()] == [new_file]
