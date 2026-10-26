"""The Workspace store opens and saves its Workspace, reports its state, and signals bookmark changes."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkKind,
    BookmarkTarget,
    LiveStreamItem,
    PlaylistItem,
    VideoItem,
    Workspace,
    WorkspaceFileError,
    WorkspaceItem,
    save_workspace,
)
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_video
from tests.helpers.workspace import content_item
from tests.helpers.workspace_store import two_playlist_store


def _saved_file(tmp_path: Path, *items: WorkspaceItem, name: str = "saved") -> Path:
    path = tmp_path / f"{name}.ax-devil.workspace"
    save_workspace(Workspace(name=name, items=items), path)
    return path


def test_opening_a_workspace_resolves_its_items_and_is_not_modified(tmp_path: Path) -> None:
    store = two_playlist_store()
    store.add_items([content_item(make_video("old.mp4"))])
    runs = PlaylistItem(label="Runs", resolver="runs")
    broken = PlaylistItem(label="Broken", resolver="missing")
    replaced: list[None] = []
    store.workspace_replaced.connect(lambda: replaced.append(None))

    store.open_workspace(_saved_file(tmp_path, runs, broken))

    assert replaced == [None]
    assert store.workspace.items == (runs, broken)
    assert store.workspace.name == "saved"
    assert not store.is_modified
    assert [content.display_name for content in store.contents()] == ["Runs / train", "Runs / test"]
    assert store.resolution(broken.id).error is not None


def test_a_failed_open_leaves_the_store_unchanged(tmp_path: Path) -> None:
    store = two_playlist_store()
    video = content_item(make_video("keep.mp4"))
    store.add_items([video])
    signals: list[str] = []
    store.workspace_replaced.connect(lambda: signals.append("replaced"))
    store.state_changed.connect(lambda: signals.append("state"))
    broken = tmp_path / "broken.ax-devil.workspace"
    broken.write_text("{", encoding="utf-8")

    with pytest.raises(WorkspaceFileError):
        store.open_workspace(broken)

    assert store.workspace.items == (video,)
    assert store.workspace.path is None
    assert store.is_modified
    assert [content.item_id for content in store.contents()] == [video.id]
    assert signals == []


def test_saving_keeps_the_name_and_clears_modified(tmp_path: Path) -> None:
    store = two_playlist_store()
    item = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([item])
    assert store.is_modified
    store.rename_workspace("Mine")
    path = tmp_path / "lot.ax-devil.workspace"

    store.save_workspace(path)

    assert store.workspace.path == path and store.workspace.name == "Mine"
    assert not store.is_modified
    store.rename_item(item.id, "Renamed")
    assert store.is_modified
    store.save_workspace()
    assert not store.is_modified
    reopened = two_playlist_store()
    reopened.open_workspace(path)
    assert [item.label for item in reopened.workspace.items] == ["Renamed"]


def test_saving_an_untitled_workspace_needs_a_path() -> None:
    with pytest.raises(ValueError, match="path"):
        two_playlist_store().save_workspace()


def test_state_changed_fires_once_per_change_of_modified_name_or_path(tmp_path: Path) -> None:
    store = two_playlist_store()
    states: list[tuple[bool, str]] = []
    store.state_changed.connect(lambda: states.append((store.is_modified, store.workspace.name)))
    first, second = PlaylistItem(label="A", resolver="runs"), PlaylistItem(label="B", resolver="runs")

    store.add_items([first])
    store.add_items([second])
    store.rename_workspace("w")
    store.save_workspace(tmp_path / "w.ax-devil.workspace")
    store.rename_item(first.id, "A2")
    store.remove_item(second.id)
    store.save_workspace()
    store.open_workspace(tmp_path / "w.ax-devil.workspace")

    assert states == [(True, "Untitled"), (True, "w"), (False, "w"), (True, "w"), (False, "w")]


def test_renaming_the_workspace_is_an_unsaved_change_until_the_old_name_returns(tmp_path: Path) -> None:
    store = two_playlist_store()
    store.save_workspace(tmp_path / "w.ax-devil.workspace")
    states: list[tuple[bool, str]] = []
    store.state_changed.connect(lambda: states.append((store.is_modified, store.workspace.name)))

    store.rename_workspace("Parking lot")
    store.rename_workspace(" Parking lot ")
    store.rename_workspace("")

    assert states == [(True, "Parking lot"), (False, "Untitled")]


def test_removing_what_was_just_added_returns_to_unmodified() -> None:
    store = two_playlist_store()
    states: list[bool] = []
    store.state_changed.connect(lambda: states.append(store.is_modified))
    item = PlaylistItem(label="A", resolver="runs")

    store.add_items([item])
    store.remove_item(item.id)

    assert states == [True, False]


def test_clearing_a_saved_label_shows_each_kinds_default_name_after_reopening(tmp_path: Path) -> None:
    video_path = tmp_path / "lot.mp4"
    video_path.write_bytes(b"")
    video = VideoItem(label="Parking lot", video=video_path)
    stream = LiveStreamItem(label="Entrance", host="camera.local")
    store = two_playlist_store()
    store.open_workspace(_saved_file(tmp_path, video, stream))

    store.rename_item(video.id, "")
    store.rename_item(stream.id, "")

    assert [item.label for item in store.workspace.items] == ["", ""]
    assert [content.display_name for content in store.contents()] == ["lot.mp4", "camera.local"]


def _recording_bookmark_signals(store: WorkspaceStore) -> list[str]:
    signals: list[str] = []
    store.bookmarks_changed.connect(lambda: signals.append("bookmarks"))
    store.state_changed.connect(lambda: signals.append("state"))
    return signals


def test_each_bookmark_edit_signals_once_and_marks_the_workspace_modified(tmp_path: Path) -> None:
    store = two_playlist_store()
    runs = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([runs])
    store.save_workspace(tmp_path / "w.ax-devil.workspace")
    target = BookmarkTarget(runs.id, "a.mp4")
    signals = _recording_bookmark_signals(store)

    bookmark = store.add_bookmark(target, 30, 1.0, BookmarkKind.ISSUE, "Missed box")
    assert bookmark is not None
    store.edit_bookmark(bookmark.id, note="Missed person")
    store.set_reviewed(target, True)
    store.remove_bookmark(bookmark.id)
    store.set_reviewed(target, False)

    assert signals == ["bookmarks", "state", "bookmarks", "bookmarks", "bookmarks", "bookmarks", "state"]
    assert not store.is_modified
    assert (store.bookmarks, store.reviewed) == ((), frozenset())


def test_bookmarks_are_saved_with_the_workspace_and_read_back(tmp_path: Path) -> None:
    store = two_playlist_store()
    runs = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([runs])
    target = BookmarkTarget(runs.id, "b.mp4")
    bookmark = store.add_bookmark(target, 12, 0.5, note="Gate")
    store.set_reviewed(target, True)
    path = tmp_path / "w.ax-devil.workspace"

    store.save_workspace(path)
    reopened = two_playlist_store()
    reopened.open_workspace(path)

    assert reopened.bookmarks == (bookmark,)
    assert reopened.reviewed == {target}
    assert not reopened.is_modified


def test_requests_that_cannot_apply_change_nothing_and_signal_nothing() -> None:
    store = two_playlist_store()
    runs, live = PlaylistItem(label="Runs", resolver="runs"), LiveStreamItem(label="Cam", host="camera")
    store.add_items([runs, live])
    target = BookmarkTarget(runs.id)
    store.set_reviewed(target, True)
    before = store.workspace
    signals = _recording_bookmark_signals(store)

    added = [
        store.add_bookmark(BookmarkTarget(live.id), 0, 0.0),
        store.add_bookmark(BookmarkTarget("missing"), 0, 0.0),
        store.add_bookmark(target, -1, 0.0),
    ]
    store.set_reviewed(BookmarkTarget(live.id), True)
    store.set_reviewed(target, True)
    store.remove_bookmark("missing")
    store.edit_bookmark("missing", note="x")

    assert added == [None, None, None]
    assert store.workspace == before
    assert signals == []


def test_removing_an_item_signals_bookmarks_only_when_it_had_some() -> None:
    store = two_playlist_store()
    marked, plain = PlaylistItem(label="A", resolver="runs"), PlaylistItem(label="B", resolver="runs")
    store.add_items([marked, plain])
    store.add_bookmark(BookmarkTarget(marked.id, "a.mp4"), 3, 0.1)
    store.set_reviewed(BookmarkTarget(plain.id, "b.mp4"), True)
    store.set_reviewed(BookmarkTarget(plain.id, "b.mp4"), False)
    changes: list[None] = []
    store.bookmarks_changed.connect(lambda: changes.append(None))

    store.remove_item(plain.id)
    assert changes == []
    store.remove_item(marked.id)

    assert changes == [None]
    assert store.bookmarks == ()


def test_replacing_the_workspace_signals_that_bookmarks_changed(tmp_path: Path) -> None:
    store = two_playlist_store()
    runs = PlaylistItem(label="Runs", resolver="runs")
    bookmark = Bookmark(target=BookmarkTarget(runs.id, "a.mp4"), frame=1, time_s=0.0)
    path = tmp_path / "w.ax-devil.workspace"
    save_workspace(Workspace(items=(runs,), bookmarks=(bookmark,)), path)
    changes: list[tuple[Bookmark, ...]] = []
    store.bookmarks_changed.connect(lambda: changes.append(store.bookmarks))

    store.open_workspace(path)
    store.replace_workspace(Workspace())

    assert changes == [(bookmark,), ()]
