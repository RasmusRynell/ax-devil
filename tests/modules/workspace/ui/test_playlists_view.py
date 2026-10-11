"""The Playlists view lists the workspace's playlists and the resolvers that can add more, and acts through the
workspace."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QLabel, QScrollArea
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.small_button import SmallButton
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
    WorkspaceBackup,
)
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.playlists_view import PlaylistListing, PlaylistState, PlaylistsView
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.contents import make_video
from tests.helpers.shortcuts import make_shortcut_manager
from tests.helpers.workspace import DummyViewer, FakePrompts, FakeResolutionContext

_OFFLINE_VIEWER = "ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget"


class _Folder:
    """A playlist resolver over a folder the test fills: one playlist with an entry per clip, two lanes in the first."""

    def __init__(self, count: int) -> None:
        self.count = count

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
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
    """Return a session showing the Playlists view, with *resolvers* under the ids of the built-in ones.

    Items resolve at once unless *in_background*.
    """
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=ItemResolver(FakeResolutionContext(resolvers=resolvers), in_background=in_background),
        backup=WorkspaceBackup(lambda: tmp_path / "workspace-backup.json"),
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "recent-workspaces.json"),
        prompts=FakePrompts(),
    )
    qtbot.addWidget(session.widget())
    session.widget().resize(1000, 900)
    session.widget().show()
    session.show_sidebar_view("playlists")
    return session


def _view(session: WorkspaceSession) -> PlaylistsView:
    view = session.widget().findChild(PlaylistsView)
    assert view is not None
    return view


def _rows(view: PlaylistsView) -> list[ListRow]:
    """Return the rows the view lists now, top to bottom."""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return view.findChildren(ListRow)


def _texts(row: ListRow) -> list[str]:
    """Return the text a row shows, title first."""
    return [
        label.full_text() if isinstance(label, ElidedLabel) else label.text()
        for label in row.findChildren(QLabel)
        if isinstance(label, ElidedLabel) or label.text()
    ]


def _row(view: PlaylistsView, title: str) -> ListRow:
    return next(row for row in _rows(view) if _texts(row)[0] == title)


def _titles(view: PlaylistsView) -> list[str]:
    return [_texts(row)[0] for row in _rows(view)]


def _click(row: ListRow) -> None:
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(row.width() // 3, row.height() // 2))


def _buttons(row: ListRow) -> list[SmallButton]:
    return row.findChildren(SmallButton)


def test_playlists_list_their_resolver_and_entries_and_expand_on_click(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager, folder_pair=_Folder(3), mot_challenge=_Folder(1))
    view = _view(session)
    assert "Night shift" not in _titles(view)

    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items(
            [
                PlaylistItem(label="Night shift", resolver="folder_pair"),
                PlaylistItem(label="MOT", resolver="mot_challenge"),
            ]
        )

    assert _texts(_row(view, "Night shift")) == ["Night shift", "Folder Pair · 3 entries"]
    assert _texts(_row(view, "MOT")) == ["MOT", "MOT Challenge · 1 entry"]
    assert "run_00.mp4" not in _titles(view)

    _click(_row(view, "Night shift"))
    assert _titles(view)[1:4] == ["run_00.mp4", "run_01.mp4", "run_02.mp4"]
    assert _texts(_row(view, "run_00.mp4")) == ["run_00.mp4", "2 lanes"]
    assert _texts(_row(view, "run_01.mp4")) == ["run_01.mp4"]

    _click(_row(view, "Night shift"))
    assert "run_00.mp4" not in _titles(view)


def test_an_expanded_playlist_lists_twenty_entries_and_counts_the_rest(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager, folder_pair=_Folder(23))
    view = _view(session)
    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([PlaylistItem(label="Day shift", resolver="folder_pair")])

    _click(_row(view, "Day shift"))

    titles = _titles(view)
    entries = [title for title in titles if title.startswith("run_")]
    assert entries == [f"run_{index:02d}.mp4" for index in range(20)]
    assert titles[titles.index("run_19.mp4") + 1] == "3 more"


def test_clicking_an_entry_opens_its_playlist_at_that_entry(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager, folder_pair=_Folder(3))
    view = _view(session)
    with patch(_OFFLINE_VIEWER, DummyViewer):
        session.add_items([PlaylistItem(label="Night shift", resolver="folder_pair")])
        _click(_row(view, "Night shift"))
        _click(_row(view, "run_02.mp4"))

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
        [rescan] = _buttons(_row(view, "Night shift"))
        assert rescan.toolTip() == "Rescan"
        rescan.click()

    assert _texts(_row(view, "Night shift")) == ["Night shift", "Folder Pair · 4 entries"]
    assert session.focused_widget() is viewer and viewer.payload is shown


def test_a_playlist_that_failed_shows_why_and_can_only_be_rescanned(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager)
    view = _view(session)
    with patch("PySide6.QtWidgets.QMessageBox.warning"):
        session.add_items([PlaylistItem(label="Archive", resolver="folder_pair")])

    row = _row(view, "Archive")
    assert _texts(row) == ["Archive", "Playlist resolver 'folder_pair' is not installed."]
    assert len(_buttons(row)) == 1
    _click(row)
    assert _titles(view).index("Archive") + 1 == _titles(view).index("Folder Pair")


def test_the_list_keeps_its_scroll_position_when_the_rows_change(qtbot: QtBot) -> None:
    """Opening an entry far down the list refreshes the rows; the list must not jump back to the top."""
    view = PlaylistsView()
    qtbot.addWidget(view)
    view.resize(300, 300)
    view.show()
    playlists = [
        PlaylistListing(str(index), str(index), f"Run {index}", "", PlaylistState.READY) for index in range(40)
    ]
    view.set_listings(playlists, [])
    scroll = view.findChild(QScrollArea)
    assert scroll is not None
    scroll_bar = scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: scroll_bar.maximum() > 0)
    position = scroll_bar.maximum() // 2
    scroll_bar.setValue(position)

    view.set_listings(playlists, [])
    qtbot.waitUntil(lambda: scroll_bar.maximum() > 0 and all(row.isVisible() for row in _rows(view)))

    assert scroll_bar.value() == position


class _Blocking:
    """A resolver that answers once the test releases it."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        assert self.release.wait(timeout=5)
        return _Folder(1).resolve(settings)


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

    row = _row(view, "Night shift")
    assert _texts(row) == ["Night shift", "Loading…"]
    assert _buttons(row) == []
    with patch(_OFFLINE_VIEWER, DummyViewer):
        blocking.release.set()
        qtbot.waitUntil(lambda: _texts(_row(view, "Night shift"))[1] == "Folder Pair · 1 entry")


def _external_resolver() -> PluginRecord:
    builtin = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "folder_pair")
    definition = PluginDefinitionBase(PLAYLIST_RESOLVER_PLUGIN_TYPE, "runs", "Experiment Runs", "Open each run.")
    return replace(builtin, definition=definition, origin="package:lab-experiments")


def _failed_resolver() -> PluginRecord:
    definition = PluginDefinitionBase(PLAYLIST_RESOLVER_PLUGIN_TYPE, "broken", "Broken", None)
    return PluginRecord(definition, Path("broken"), "package:broken", PluginStatus.FAILED, error="import failed")


def test_add_from_lists_the_loaded_resolvers_whenever_the_view_is_shown(
    qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(qtbot, tmp_path, render_catalog_manager)
    view = _view(session)
    builtins = RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE)
    assert _titles(view) == [record.definition.display_name for record in builtins]

    loaded = [*builtins, _external_resolver(), _failed_resolver()]
    monkeypatch.setattr(RuntimePluginRegistry, "get_plugins", lambda _plugin_type: loaded)
    session.show_sidebar_view("workspace")
    session.show_sidebar_view("playlists")

    assert _titles(view) == [*(record.definition.display_name for record in builtins), "Experiment Runs"]
    assert _texts(_row(view, "Experiment Runs"))[1] == "From lab-experiments. Open each run."
    assert _texts(_row(view, "Folder Pair"))[1] == "Match videos and overlays from two folders by file name."


def test_add_opens_the_add_playlist_dialog_with_that_resolver_chosen(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    window = MainWindow(shortcut_manager=make_shortcut_manager(), render_catalog_manager=render_catalog_manager)
    qtbot.addWidget(window)
    window.show()
    window._workspace_session.show_sidebar_view("playlists")
    view = window.findChild(PlaylistsView)
    assert view is not None
    chosen: list[str] = []
    original_exec = BaseDialog.exec

    def note_choice(dialog: BaseDialog) -> int:
        combos = [combo for combo in dialog.findChildren(QComboBox) if combo.itemText(0) == "Select a source"]
        chosen.append(combos[0].currentText())
        QTimer.singleShot(0, dialog.reject)
        return original_exec(dialog)

    requested: list[str] = []
    view.add_requested.connect(requested.append)
    with patch.object(BaseDialog, "exec", note_choice):
        [add] = _buttons(_row(view, "MOT Challenge"))
        add.click()

    assert requested == ["mot_challenge"]
    assert chosen == ["MOT Challenge"]
