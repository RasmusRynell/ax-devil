"""The Bookmarks view lists the workspace's bookmarks by target, and adds, opens, edits, and exports them."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLabel, QLineEdit, QMenu, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.chrome.chip import Chip
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkKind,
    BookmarkPosition,
    BookmarkTarget,
    FileVideoSourceSpec,
    LiveStreamItem,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistResolver,
    PlaylistSettings,
    RecentWorkspaces,
    SeekableVideoContent,
    VideoItem,
)
from ax_devil.modules.workspace.ui.bookmarks_view import BOOKMARKS_VIEW_ID, BookmarksView
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.workspace import DummyViewer, FakePrompts, FakeResolutionContext

_OFFLINE_VIEWER = "ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget"
_LIVE_VIEWER = "ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget"


def _video(name: str) -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")))


class _NightShift(PlaylistResolver):
    """A resolver that finds the same two runs every time."""

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        entries = tuple(
            PlaylistEntry(lanes=_video(name).standalone_lanes(), default_considered=True)
            for name in ("run_01.mp4", "run_02.mp4")
        )
        return [PlaylistContent(display_name="night", entries=entries)]


@pytest.fixture
def session(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> Iterator[WorkspaceSession]:
    """A shown session whose viewers are doubles, with a video, a playlist, and a live stream added and opened."""
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=ItemResolver(FakeResolutionContext(resolvers={"night": _NightShift()}), in_background=False),
        unsaved_folder=tmp_path / "state" / "unsaved-workspaces",
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "state" / "recent-workspaces.json"),
        prompts=FakePrompts(),
    )
    qtbot.addWidget(session.widget())
    session.widget().resize(900, 600)
    session.widget().show()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_OFFLINE_VIEWER, DummyViewer)
        patch.setattr(_LIVE_VIEWER, DummyViewer)
        session.add_items([VideoItem(video=clip)])
        session.add_items([PlaylistItem(label="Night shift", resolver="night")])
        session.add_items([LiveStreamItem(host="192.0.2.10", label="Entrance")])
        yield session
    session.cleanup()


def _store(session: WorkspaceSession) -> WorkspaceStore:
    return session._workspace_store


def _items(session: WorkspaceSession) -> tuple[str, str, str]:
    """Return the ids of the video, the playlist, and the live stream item."""
    video, playlist, live = (item.id for item in _store(session).workspace.items)
    return video, playlist, live


def _view(session: WorkspaceSession) -> BookmarksView:
    view = session.widget().findChild(BookmarksView)
    assert view is not None
    return view


def _rows(session: WorkspaceSession) -> list[ListRow]:
    """Return the rows on the view, top to bottom."""
    view = _view(session)
    rows = [row for row in view.findChildren(ListRow) if not row.isHidden()]
    return sorted(rows, key=lambda row: row.mapTo(view, QPoint(0, 0)).y())


def _texts(row: ListRow) -> list[str]:
    """Return the title and subtitle of *row*."""
    return [label.full_text() for label in row.findChildren(ElidedLabel)]


def _listed(session: WorkspaceSession) -> list[list[str]]:
    return [_texts(row) for row in _rows(session)]


def _row(session: WorkspaceSession, title: str) -> ListRow:
    return next(row for row in _rows(session) if _texts(row)[0] == title)


def _chip(widget: ListRow | BookmarksView, text: str) -> Chip:
    return next(chip for chip in widget.findChildren(Chip) if chip.text() == text and not chip.isHidden())


def _click(widget: QWidget) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(widget.width() // 2, widget.height() // 2))


def _context_menu(row: ListRow, *choice: str) -> None:
    """Right-click *row* and choose the entry at the path *choice*, such as ("Kind", "Issue")."""

    def choose() -> None:
        menu = next(menu for menu in _view_of(row).findChildren(QMenu) if menu.isVisible())
        for index, text in enumerate(choice):
            action = next(action for action in menu.actions() if action.text() == text)
            if index < len(choice) - 1:
                submenu = action.menu()
                assert isinstance(submenu, QMenu)
                menu = submenu
            else:
                action.trigger()
        for visible in _view_of(row).findChildren(QMenu):
            visible.close()

    QTimer.singleShot(0, choose)
    row.customContextMenuRequested.emit(QPoint(4, 4))


def _view_of(row: ListRow) -> BookmarksView:
    parent = row.parentWidget()
    while not isinstance(parent, BookmarksView):
        assert parent is not None
        parent = parent.parentWidget()
    return parent


def _add(session: WorkspaceSession, target: BookmarkTarget, frame: int, kind: BookmarkKind, note: str) -> Bookmark:
    bookmark = _store(session).add_bookmark(target, frame, frame / 30, kind, note)
    assert bookmark is not None
    return bookmark


def _bookmark_both(session: WorkspaceSession) -> None:
    video, playlist, _live = _items(session)
    _add(session, BookmarkTarget(video), 30, BookmarkKind.ISSUE, "Car 8 deleted")
    _add(session, BookmarkTarget(video), 20, BookmarkKind.QUESTION, "Bicycle 6 disappears")
    _add(session, BookmarkTarget(playlist, "night/run_02.mp4"), 54, BookmarkKind.EXAMPLE, "Clean tracks")


def test_bookmarks_are_listed_under_their_video_or_playlist_entry_in_frame_order(session: WorkspaceSession) -> None:
    _bookmark_both(session)

    assert _listed(session) == [
        ["clip.mp4"],
        ["Bicycle 6 disappears", "0:00.67 · frame 20 · Question"],
        ["Car 8 deleted", "0:01.00 · frame 30 · Issue"],
        ["Night shift › run_02.mp4"],
        ["Clean tracks", "0:01.80 · frame 54 · Example"],
    ]
    assert [chip.text() for row in (_rows(session)[0], _rows(session)[3]) for chip in row.findChildren(Chip)] == [
        "To review",
        "To review",
    ]


def test_without_bookmarks_the_view_says_so_and_offers_no_search(session: WorkspaceSession) -> None:
    view = _view(session)

    assert _rows(session) == []
    assert [label.text() for label in view.findChildren(QLabel) if label.isVisibleTo(view)].count("No bookmarks") == 1
    search = view.findChild(QLineEdit)
    assert search is not None and not search.isVisibleTo(view)
    assert not any(chip.isVisibleTo(view) for chip in view.findChildren(Chip))
    assert not next(button for button in view.findChildren(SmallButton) if button.text() == "Export CSV").isEnabled()


def test_kind_chips_and_the_search_narrow_the_list(session: WorkspaceSession) -> None:
    _bookmark_both(session)
    view = _view(session)

    _click(_chip(view, "Issue"))
    assert [texts[0] for texts in _listed(session)] == ["clip.mp4", "Car 8 deleted"]

    _click(_chip(view, "All"))
    search = view.findChild(QLineEdit)
    assert search is not None
    search.setText("BICYCLE")
    assert [texts[0] for texts in _listed(session)] == ["clip.mp4", "Bicycle 6 disappears"]

    search.setText("night")  # A group's name finds all its bookmarks.
    assert [texts[0] for texts in _listed(session)] == ["Night shift › run_02.mp4", "Clean tracks"]

    _click(_chip(view, "Issue"))
    assert _rows(session) == []


def test_the_review_chip_marks_the_target_reviewed_and_back(session: WorkspaceSession) -> None:
    _bookmark_both(session)
    video, _playlist, _live = _items(session)

    _click(_chip(_rows(session)[0], "To review"))
    assert _store(session).reviewed == {BookmarkTarget(video)}
    assert [chip.text() for chip in _rows(session)[0].findChildren(Chip)] == ["Reviewed"]

    _click(_chip(_rows(session)[0], "Reviewed"))
    assert _store(session).reviewed == frozenset()


def test_a_reviewed_target_stays_listed_after_its_last_bookmark_is_removed_until_the_mark_is_cleared(
    session: WorkspaceSession,
) -> None:
    video, _playlist, _live = _items(session)
    bookmark = _add(session, BookmarkTarget(video), 30, BookmarkKind.ISSUE, "Car 8 deleted")
    _click(_chip(_rows(session)[0], "To review"))

    _store(session).remove_bookmark(bookmark.id)

    assert _listed(session) == [["clip.mp4"]]
    _click(_chip(_rows(session)[0], "Reviewed"))
    assert _store(session).reviewed == frozenset()
    assert _rows(session) == []


def test_a_saved_workspace_reopens_on_its_bookmarks_and_a_bookmark_opens_its_entry_at_its_frame(
    session: WorkspaceSession, tmp_path: Path
) -> None:
    _bookmark_both(session)
    path = tmp_path / "spots.axdevil"
    _store(session).save_workspace(path)
    session.widget().show_view("workspace")

    session.lifecycle.open_workspace(path)

    assert session.widget()._sidebar.current_view_id() == BOOKMARKS_VIEW_ID
    assert _rows(session)
    _click(_row(session, "Clean tracks"))

    viewer = session.focused_widget()
    assert isinstance(viewer, DummyViewer)
    assert isinstance(viewer.payload, PlaylistContent) and viewer.payload.display_name == "Night shift"
    assert (viewer.start_index, viewer.start_frame) == (1, 54)


def test_a_bookmark_whose_entry_is_gone_is_shown_missing_and_opens_nothing(session: WorkspaceSession) -> None:
    _video_id, playlist, _live = _items(session)
    _add(session, BookmarkTarget(playlist, "night/run_09.mp4"), 12, BookmarkKind.NOTE, "")
    opened = session.focused_widget()

    assert _listed(session) == [["Night shift › night/run_09.mp4"], ["Frame 12", "Missing"]]
    assert _rows(session)[0].findChildren(Chip) == []
    _click(_rows(session)[1])
    assert session.focused_widget() is opened


def test_the_menu_renames_changes_the_kind_and_removes_a_bookmark(
    session: WorkspaceSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bookmark_both(session)
    names = iter(["Bike lost", "Frame 30"])

    def enter_name(dialog: RenameDialog) -> int:
        dialog._name_edit.setText(next(names))
        return RenameDialog.DialogCode.Accepted

    monkeypatch.setattr(RenameDialog, "exec", enter_name)

    def notes() -> list[tuple[str, BookmarkKind]]:
        return [(bookmark.note, bookmark.kind) for bookmark in _store(session).bookmarks]

    _context_menu(_row(session, "Bicycle 6 disappears"), "Rename")
    _context_menu(_row(session, "Bike lost"), "Kind", "Issue")
    _context_menu(_row(session, "Car 8 deleted"), "Rename")  # Its default label clears the note.
    assert notes() == [
        ("", BookmarkKind.ISSUE),
        ("Bike lost", BookmarkKind.ISSUE),
        ("Clean tracks", BookmarkKind.EXAMPLE),
    ]
    assert "Frame 30" in [texts[0] for texts in _listed(session)]

    _context_menu(_row(session, "Clean tracks"), "Remove")
    assert notes() == [("", BookmarkKind.ISSUE), ("Bike lost", BookmarkKind.ISSUE)]
    assert "Night shift › run_02.mp4" not in [texts[0] for texts in _listed(session)]


def test_the_shortcut_bookmarks_the_frame_of_the_focused_video_and_shows_the_view(session: WorkspaceSession) -> None:
    video, _playlist, _live = _items(session)
    [content] = _store(session).resolution(video).contents
    session.widget().show_view("workspace")
    session._workspace_controller.open_content(content)
    viewer = session.focused_widget()
    assert isinstance(viewer, DummyViewer)
    viewer.position = BookmarkPosition(content, 0, 12, 0.4)

    session.bookmark_focused_viewer()

    [bookmark] = _store(session).bookmarks
    assert (bookmark.target, bookmark.frame, bookmark.time_s) == (BookmarkTarget(video), 12, 0.4)
    assert (bookmark.kind, bookmark.note) == (BookmarkKind.NOTE, "")
    assert session.widget()._sidebar.current_view_id() == BOOKMARKS_VIEW_ID
    assert session.focused_widget() is viewer


def test_a_live_stream_or_no_viewer_is_never_bookmarked(session: WorkspaceSession) -> None:
    session.widget().show_view("workspace")
    session.bookmark_focused_viewer()  # The live stream, added last, is on screen.
    session._center_area.clear_all_widgets()
    session.bookmark_focused_viewer()

    assert _store(session).bookmarks == ()
    assert session.widget()._sidebar.current_view_id() == "workspace"


def test_exporting_writes_every_bookmark_to_the_chosen_csv(
    session: WorkspaceSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _bookmark_both(session)
    _click(_chip(_view(session), "Issue"))  # Filters do not limit the export.
    path = tmp_path / "out.csv"
    suggested: list[str] = []

    def choose(_parent: object, _title: str, start: str, _filter: str) -> tuple[str, str]:
        suggested.append(start)
        return str(path), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    _click(next(button for button in _view(session).findChildren(SmallButton) if button.text() == "Export CSV"))

    assert Path(suggested[0]).name == "Untitled bookmarks.csv"
    with path.open(encoding="utf-8", newline="") as file:
        assert list(csv.reader(file)) == [
            ["target", "entry", "frame", "time", "kind", "note"],
            ["clip.mp4", "", "20", "0.667", "Question", "Bicycle 6 disappears"],
            ["clip.mp4", "", "30", "1.000", "Issue", "Car 8 deleted"],
            ["Night shift", "run_02.mp4", "54", "1.800", "Example", "Clean tracks"],
        ]
