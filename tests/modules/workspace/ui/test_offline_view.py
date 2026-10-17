"""The Offline view lists the workspace's videos and playlists and the resolvers that can add more, and acts through the
workspace."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import NEVER, CancelToken
from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.plugin_system import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PluginDefinitionBase,
    PluginRecord,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import (
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistResolver,
    PlaylistSettings,
    RecentWorkspaces,
    VideoItem,
)
from ax_devil.modules.workspace.ui.browser_rows import BrowserSection, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.offline_view import OfflineView
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.contents import make_video
from tests.helpers.section_tree import (
    captions,
    double_click,
    press,
    row,
    row_action,
    row_texts,
    texts,
    titles,
)
from tests.helpers.shortcuts import make_shortcut_manager
from tests.helpers.widgets import button
from tests.helpers.workspace import DummyViewer, FakePrompts, FakeResolutionContext

_OFFLINE_VIEWER = "ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget"


class _Folder:
    """A playlist resolver over a folder the test fills: one playlist with an entry per clip, two lanes in the first."""

    def __init__(self, count: int) -> None:
        self.count = count

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        lanes = [make_video(f"run_{index:02d}.mp4").standalone_lanes() for index in range(self.count)]
        lanes[0] = lanes[0] * 2
        return [PlaylistContent(display_name="Runs", entries=tuple(PlaylistEntry(lane, True) for lane in lanes))]


def _session(
    qtbot: QtBot,
    tmp_path: Path,
    render_catalog_manager: SceneRenderCatalogManager,
    in_background: bool = False,
    **resolvers: PlaylistResolver,
) -> WorkspaceSession:
    """Return a session showing the Offline view, with *resolvers* under the ids of the built-in ones.

    Items resolve at once unless *in_background*.
    """
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=ItemResolver(FakeResolutionContext(resolvers=resolvers), in_background=in_background),
        unsaved_folder=tmp_path / "unsaved-workspaces",
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "recent-workspaces.json"),
        prompts=FakePrompts(),
    )
    qtbot.addWidget(session.widget())
    session.widget().resize(1000, 900)
    session.widget().show()
    session.show_sidebar_view("offline")
    return session


def _view(session: WorkspaceSession) -> OfflineView:
    view = session.widget().findChild(OfflineView)
    assert view is not None
    return view


def _texts(view: OfflineView, title: str) -> list[str]:
    return row_texts(row(view.tree, title))


def _resolvers() -> list[PluginRecord]:
    return RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE)


def _resolver_names() -> list[str]:
    return [record.definition.display_name for record in _resolvers()]


def test_playlists_list_their_entries_from_the_start(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager, folder_pair=_Folder(3), mot_challenge=_Folder(1))
    view = _view(session)
    assert captions(view.tree) == [f"Add from, {len(_resolver_names())}"]

    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items(
            [
                PlaylistItem(label="Night shift", resolver="folder_pair"),
                PlaylistItem(label="MOT", resolver="mot_challenge"),
            ]
        )

    assert captions(view.tree)[0] == "Playlists, 2"
    assert _texts(view, "Night shift") == ["Night shift"]
    assert _texts(view, "MOT") == ["MOT"]
    shown = titles(view.tree)
    assert "run_02.mp4" in shown, "a playlist shows its entries without being expanded"
    assert shown[shown.index("MOT") + 1] == "run_00.mp4"


def test_videos_are_listed_beside_the_playlists_and_both_are_counted(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager, folder_pair=_Folder(2))
    view = _view(session)

    with patch(_OFFLINE_VIEWER, DummyViewer), patch("PySide6.QtWidgets.QMessageBox.warning"):
        session.add_items(
            [VideoItem(video=tmp_path / "gate.mp4"), PlaylistItem(label="Night shift", resolver="folder_pair")]
        )

    assert captions(view.tree)[:2] == ["Videos, 1", "Playlists, 1"]
    assert "2" in texts(view)


def test_the_location_order_lists_rows_by_where_they_come_from_and_those_without_one_last(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager)
    view = _view(session)
    view.set_sections(
        (
            WorkspaceBrowserSection(
                BrowserSection.VIDEOS,
                (
                    WorkspaceBrowserRow("a", "a.mp4", "video", location="/data/zeta/a.mp4"),
                    WorkspaceBrowserRow("n", "no-location.mp4", "video"),
                    WorkspaceBrowserRow("b", "b.mp4", "video", location="/data/Alpha/b.mp4"),
                ),
            ),
        )
    )
    assert titles(view.tree) == ["a.mp4", "no-location.mp4", "b.mp4"]

    [sort] = [button for button in view.findChildren(MenuButton) if button.text() == "Sort"]
    menu = sort.menu()
    assert menu is not None
    [location] = [action for action in menu.actions() if action.text() == "Location"]
    location.trigger()

    assert titles(view.tree) == ["b.mp4", "a.mp4", "no-location.mp4"]


def test_add_video_under_the_tree_asks_the_session_to_add_a_video(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager)

    with qtbot.waitSignal(session.add_video_requested, timeout=1000):
        button(_view(session), "Add video").click()


def test_add_video_opens_the_add_video_dialog(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> None:
    window = MainWindow(shortcut_manager=make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    window.show()
    window._workspace_session.show_sidebar_view("offline")
    view = window.findChild(OfflineView)
    assert view is not None
    opened: list[BaseDialog] = []

    def reject(dialog: BaseDialog) -> int:
        opened.append(dialog)
        return BaseDialog.DialogCode.Rejected

    with patch.object(BaseDialog, "exec", reject):
        button(view, "Add video").click()

    assert [type(dialog).__name__ for dialog in opened] == ["AddVideoDialog"]


def test_double_clicking_an_entry_opens_its_playlist_at_that_entry(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager, folder_pair=_Folder(3))
    view = _view(session)
    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([PlaylistItem(label="Night shift", resolver="folder_pair")])
        double_click(view.tree, "run_02.mp4")

    viewer = session.focused_widget()
    assert isinstance(viewer, DummyViewer)
    assert isinstance(viewer.payload, PlaylistContent) and viewer.payload.display_name == "Night shift"
    assert viewer.start_index == 2


def test_rescan_picks_up_new_entries_without_opening_or_changing_a_viewer(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    folder = _Folder(2)
    session = _session(qtbot, tmp_path, render_catalog_manager, folder_pair=folder)
    view = _view(session)
    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([PlaylistItem(label="Night shift", resolver="folder_pair")])
        viewer = session.focused_widget()
        assert isinstance(viewer, DummyViewer)
        shown = viewer.payload
        folder.count = 4
        assert row_action(row(view.tree, "Night shift")) == "Rescan"
        press(view.tree, "Night shift")

    assert "4 entries" in row(view.tree, "Night shift").toolTip()
    assert session.focused_widget() is viewer and viewer.payload is shown


def test_a_playlist_that_failed_says_why_on_hover_and_can_only_be_rescanned(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager)
    view = _view(session)
    with patch("PySide6.QtWidgets.QMessageBox.warning"):
        session.add_items([PlaylistItem(label="Archive", resolver="folder_pair")])

    failed = row(view.tree, "Archive")
    assert row_texts(failed) == ["Archive"]
    assert "is not installed" in failed.toolTip()
    assert row_action(failed) == "Rescan"


class _Blocking:
    """A resolver that answers once the test releases it."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        assert self.release.wait(timeout=5)
        return _Folder(1).resolve(settings, NEVER)


@pytest.fixture
def blocking() -> Iterator[_Blocking]:
    resolver = _Blocking()
    yield resolver
    resolver.release.set()


def test_a_playlist_still_resolving_shows_loading_and_no_actions(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager, blocking: _Blocking
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager, in_background=True, folder_pair=blocking)
    view = _view(session)

    session.add_items([PlaylistItem(label="Night shift", resolver="folder_pair")])

    assert _texts(view, "Night shift") == ["Night shift"]
    assert row_action(row(view.tree, "Night shift")) == ""
    with patch(_OFFLINE_VIEWER, DummyViewer):
        blocking.release.set()
        qtbot.waitUntil(lambda: row_action(row(view.tree, "Night shift")) == "Rescan")


def _external_resolver() -> PluginRecord:
    builtin = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "folder_pair")
    definition = PluginDefinitionBase(PLAYLIST_RESOLVER_PLUGIN_TYPE, "runs", "Experiment Runs", "Open each run.")
    return replace(builtin, definition=definition, package="lab-experiments")


def _failed_resolver() -> PluginRecord:
    definition = PluginDefinitionBase(PLAYLIST_RESOLVER_PLUGIN_TYPE, "broken", "Broken", None)
    return PluginRecord(definition, "broken", PluginStatus.FAILED, error="import failed")


def test_add_from_lists_the_loaded_resolvers_whenever_the_view_is_shown(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager)
    view = _view(session)
    builtins = _resolver_names()
    assert titles(view.tree) == builtins

    loaded = [*_resolvers(), _external_resolver(), _failed_resolver()]
    monkeypatch.setattr(RuntimePluginRegistry, "get_plugins", lambda _plugin_type: loaded)
    session.show_sidebar_view("workspace")
    session.show_sidebar_view("offline")

    assert titles(view.tree) == [*builtins, "Experiment Runs"]
    assert {title: row_action(row(view.tree, title)) for title in titles(view.tree)} == dict.fromkeys(
        titles(view.tree), "Add"
    )
    external = row(view.tree, "Experiment Runs")
    assert row_texts(external) == ["Experiment Runs"]
    assert "From lab-experiments." in external.toolTip()
    assert "Match videos and overlays from two folders by file name." in row(view.tree, "Folder Pair").toolTip()


def test_add_opens_the_add_playlist_dialog_with_that_resolver_chosen(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    window = MainWindow(shortcut_manager=make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    window.show()
    window._workspace_session.show_sidebar_view("offline")
    view = window.findChild(OfflineView)
    assert view is not None
    chosen: list[str] = []
    original_exec = BaseDialog.exec

    def note_choice(dialog: BaseDialog) -> int:
        combos = [combo for combo in dialog.findChildren(QComboBox) if combo.itemText(0) == "Select a source"]
        chosen.append(combos[0].currentText())
        QTimer.singleShot(0, dialog.reject)
        return original_exec(dialog)

    with patch.object(BaseDialog, "exec", note_choice):
        press(view.tree, "MOT Challenge")

    assert chosen == ["MOT Challenge"]
