from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QMenu
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.catalog_viewer import CatalogViewerWindow, close_catalog_viewer
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.workspace.core import VideoItem, WorkspaceBackup
from ax_devil.modules.workspace.ui.session import WorkspaceSession
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

    def focused_widget(self) -> _FocusedWidget | None:
        """Return the focused viewer widget."""
        return self._focused_widget

    def focused_offline_viewer(self) -> _OfflineViewer | None:
        """Return the focused offline viewer."""
        return self._offline_viewer

    def cleanup(self) -> None:
        """Match the workspace session teardown API."""


def _make_shortcut_manager() -> ShortcutManager:
    """Return a registered shortcut manager for MainWindow tests."""
    manager = ShortcutManager()
    manager.register_defaults()
    return manager


def test_play_pause_shortcut_routes_to_focused_viewer_widget(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    manager = _make_shortcut_manager()
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
    manager = _make_shortcut_manager()
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
    manager = _make_shortcut_manager()
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


def test_render_catalogs_action_is_in_view_menu_and_opens_the_catalog_viewer(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    manager = _make_shortcut_manager()
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
    manager = _make_shortcut_manager()
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
    window = MainWindow(shortcut_manager=_make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
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
    manager = _make_shortcut_manager()
    window = MainWindow(
        shortcut_manager=manager, render_catalog_manager=render_catalog_manager, use_custom_frame=use_custom_frame
    )
    qtbot.addWidget(window)
    session = window._workspace_session
    session._prompts = FakePrompts(save_path=tmp_path / "Parking lot")
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

    def shown_title() -> str:
        if not use_custom_frame:
            return window.windowTitle()
        label = window.findChild(QLabel, "AxDevilTitleLabel")
        assert label is not None
        return label.text()

    assert shown_title() == "Untitled — ax-devil"
    video = tmp_path / "lot.mp4"
    video.write_bytes(b"")
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        session.add_items([VideoItem(video=video)])
    assert shown_title() == "● Untitled — ax-devil"

    manager.get_action("app.save_workspace").trigger()

    assert shown_title() == "Parking lot — ax-devil"


def test_quitting_keeps_the_workspace_even_without_closing_the_window(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> None:
    """Quitting after an unexpected error skips closing the window; unsaved edits are still kept."""
    window = MainWindow(shortcut_manager=_make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    session = window._workspace_session
    session._backup = WorkspaceBackup(lambda: tmp_path / "workspace-backup.json")
    video = tmp_path / "lot.mp4"
    video.write_bytes(b"")
    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        session.add_items([VideoItem(video=video)])

    app = QApplication.instance()
    assert app is not None
    app.aboutToQuit.emit()

    current, _saved = WorkspaceBackup(lambda: tmp_path / "workspace-backup.json").restore()
    assert current.items == session._workspace_store.workspace.items
