"""The Workspace store: items as the truth, their resolved Content, exclusions, and one signal per change."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QObject
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import NEVER, CancelToken
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.video_viewer.offline_viewer_navigation import OfflineViewerNavigation
from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkKind,
    BookmarkTarget,
    ConsiderationItemRef,
    Content,
    EntryLane,
    ItemResolution,
    LiveStreamItem,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    VideoItem,
    Workspace,
    WorkspaceFileError,
    WorkspaceItem,
    save_workspace,
)
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_playlist, make_video
from tests.helpers.workspace import FakeFileWatcher, FakeResolutionContext, FakeResolver, content_item, inline_resolver


class _TwoPlaylists(FakeResolver):
    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        return [make_playlist("train"), make_playlist("test")]


def _playlist(store: WorkspaceStore, index: int = 0) -> PlaylistContent:
    return cast(PlaylistContent, store.contents()[index])


def _store() -> WorkspaceStore:
    return WorkspaceStore(inline_resolver(FakeResolutionContext(resolvers={"runs": _TwoPlaylists()})))


def test_adding_items_appends_then_resolves_them_with_one_signal_each_after_the_state_is_updated() -> None:
    store = _store()
    first, second = content_item(make_video("first.mp4")), content_item(make_video("second.mp4"))
    batches: list[list[WorkspaceItem]] = []
    resolved: list[tuple[list[WorkspaceItem], bool]] = []

    def record_added(items: list[WorkspaceItem]) -> None:
        assert store.workspace.items == (first, second)
        batches.append(items)

    def record_resolved(resolutions: list[ItemResolution], added: bool) -> None:
        assert [content.display_name for content in store.contents()] == ["first.mp4", "second.mp4"]
        resolved.append(([resolution.item for resolution in resolutions], added))

    store.items_added.connect(record_added)
    store.items_resolved.connect(record_resolved)

    store.add_items([first, second])

    assert batches == [[first, second]]
    assert resolved == [([first, second], True)]
    assert store.workspace.items == (first, second)
    assert [content.item_id for content in store.contents()] == [first.id, second.id]


def test_an_item_that_cannot_resolve_stays_with_its_reason_and_others_still_open() -> None:
    store = _store()
    broken = PlaylistItem(label="Exp 3", resolver="missing")
    video = content_item(make_video("clip.mp4"))

    store.add_items([broken, video])

    assert store.workspace.items == (broken, video)
    error = store.resolution(broken.id).error
    assert error is not None and "'missing' is not installed" in str(error)
    assert [content.display_name for content in store.contents()] == ["clip.mp4"]


def test_removing_an_item_removes_all_its_content_and_exclusions_with_one_signal() -> None:
    store = _store()
    runs = PlaylistItem(label="Runs", resolver="runs")
    kept = content_item(make_video("kept.mp4"))
    store.add_items([runs, kept])
    excluded = _playlist(store, 1).entry_consideration_ref(0)
    store.set_item_considered(excluded, False)
    removed: list[WorkspaceItem] = []

    def record_removed(item: WorkspaceItem) -> None:
        assert store.workspace.items == (kept,)
        removed.append(item)

    store.item_removed.connect(record_removed)

    store.remove_item(runs.id)
    store.remove_item(runs.id)

    assert removed == [runs]
    assert [content.display_name for content in store.contents()] == ["kept.mp4"]
    assert store.is_item_considered(excluded)


def test_renaming_an_item_renames_its_content_and_keeps_its_ids_and_exclusions() -> None:
    store = _store()
    video = content_item(make_video("clip.mp4", overlay_count=2), label="Clip")
    store.add_items([video])
    [before] = store.contents()
    lane = before.lane_consideration_ref(0, 1)
    store.set_item_considered(lane, False)
    renamed: list[WorkspaceItem] = []
    store.item_renamed.connect(renamed.append)

    store.rename_item(video.id, "Gate")
    store.rename_item("missing", "Other")

    assert [(item.id, item.label) for item in renamed] == [(video.id, "Gate")]
    assert [item.label for item in store.workspace.items] == ["Gate"]
    [after] = store.contents()
    assert after.content_id == before.content_id
    assert not store.is_item_considered(lane)


def test_playlist_content_display_names_follow_a_renamed_item() -> None:
    store = _store()
    runs = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([runs])

    store.rename_item(runs.id, "Exp 3")

    assert [content.display_name for content in store.contents()] == ["Exp 3 / train", "Exp 3 / test"]


def test_the_workspace_is_modified_only_while_it_differs_from_the_saved_one() -> None:
    store = _store()
    item = content_item(make_video("clip.mp4"))
    assert not store.is_modified

    store.add_items([item])
    assert store.is_modified

    store.remove_item(item.id)
    assert not store.is_modified


def _playlist_with_hidden_parts() -> Content:
    lane = make_video("a.mp4").standalone_lanes()[0]
    hidden_lane = EntryLane(display_name=lane.display_name, video=lane.video, default_considered=False)
    return PlaylistContent(
        display_name="Suite",
        entries=(
            PlaylistEntry(lanes=(lane,), default_considered=False),
            PlaylistEntry(lanes=(lane, hidden_lane), default_considered=True),
        ),
    )


def test_content_defaults_seed_exclusions_and_changes_signal_only_when_they_change() -> None:
    store = _store()
    store.add_items([content_item(_playlist_with_hidden_parts())])
    playlist = _playlist(store)
    entry = playlist.entry_consideration_ref(0)
    hidden_lane = playlist.lane_consideration_ref(1, 1)
    changes: list[tuple[ConsiderationItemRef, bool]] = []
    store.item_consideration_changed.connect(lambda ref, considered: changes.append((ref, considered)))

    assert not store.is_item_considered(entry)
    assert not store.is_item_considered(hidden_lane)
    store.set_item_considered(entry, True)
    store.set_item_considered(entry, True)
    store.set_item_considered(entry, False)

    assert changes == [(entry, True), (entry, False)]


def test_unknown_and_out_of_range_refs_are_ignored() -> None:
    store = _store()
    store.add_items([content_item(make_playlist())])
    playlist = _playlist(store)
    changes: list[object] = []
    store.item_consideration_changed.connect(lambda *args: changes.append(args))

    for ref in (
        ConsiderationItemRef("nonexistent", "", 0),
        playlist.lane_consideration_ref(0, 99),
    ):
        store.set_item_considered(ref, False)
        assert store.is_item_considered(ref)
    assert changes == []
    assert store.is_item_considered(playlist.lane_consideration_ref(0, 0))


class _CountingResolver(FakeResolver):
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        self.calls += 1
        return [make_playlist("train"), make_playlist("test")]


def test_renaming_an_item_names_its_content_again_without_running_its_resolver() -> None:
    resolver = _CountingResolver()
    store = WorkspaceStore(inline_resolver(FakeResolutionContext(resolvers={"runs": resolver})))
    runs = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([runs])
    before = [content.content_id for content in store.contents()]

    store.rename_item(runs.id, "Exp 3")
    store.rename_item(runs.id, "Exp 4")

    assert resolver.calls == 1
    assert [content.content_id for content in store.contents()] == before
    assert [content.display_name for content in store.contents()] == ["Exp 4 / train", "Exp 4 / test"]


def test_renaming_an_unresolved_item_only_relabels_it() -> None:
    store = _store()
    broken = PlaylistItem(label="Exp 3", resolver="missing")
    store.add_items([broken])
    error = store.resolution(broken.id).error
    renamed: list[WorkspaceItem] = []
    store.item_renamed.connect(renamed.append)

    store.rename_item(broken.id, "Exp 4")

    assert [item.label for item in renamed] == ["Exp 4"]
    assert store.workspace.items[0].label == "Exp 4"
    assert store.resolution(broken.id).error is error
    assert store.contents() == ()


def _saved_file(tmp_path: Path, *items: WorkspaceItem, name: str = "saved") -> Path:
    path = tmp_path / f"{name}.ax-devil.workspace"
    save_workspace(Workspace(name=name, items=items), path)
    return path


def test_opening_a_workspace_resolves_its_items_and_is_not_modified(tmp_path: Path) -> None:
    store = _store()
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
    store = _store()
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
    store = _store()
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
    reopened = _store()
    reopened.open_workspace(path)
    assert [item.label for item in reopened.workspace.items] == ["Renamed"]


def test_saving_an_untitled_workspace_needs_a_path() -> None:
    with pytest.raises(ValueError, match="path"):
        _store().save_workspace()


def test_state_changed_fires_once_per_change_of_modified_name_or_path(tmp_path: Path) -> None:
    store = _store()
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
    store = _store()
    store.save_workspace(tmp_path / "w.ax-devil.workspace")
    states: list[tuple[bool, str]] = []
    store.state_changed.connect(lambda: states.append((store.is_modified, store.workspace.name)))

    store.rename_workspace("Parking lot")
    store.rename_workspace(" Parking lot ")
    store.rename_workspace("")

    assert states == [(True, "Parking lot"), (False, "Untitled")]


def test_removing_what_was_just_added_returns_to_unmodified() -> None:
    store = _store()
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
    store = _store()
    store.open_workspace(_saved_file(tmp_path, video, stream))

    store.rename_item(video.id, "")
    store.rename_item(stream.id, "")

    assert [item.label for item in store.workspace.items] == ["", ""]
    assert [content.display_name for content in store.contents()] == ["lot.mp4", "camera.local"]


class _BlockingResolver(FakeResolver):
    """Resolve to two playlists once the test releases it, recording the thread it ran on."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.threads: list[threading.Thread] = []

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        self.threads.append(threading.current_thread())
        assert self.release.wait(timeout=5)
        return [make_playlist("train"), make_playlist("test")]


@pytest.fixture()
def blocking() -> Iterator[_BlockingResolver]:
    resolver = _BlockingResolver()
    yield resolver
    resolver.release.set()


def _background_store(resolver: _BlockingResolver) -> WorkspaceStore:
    return WorkspaceStore(ItemResolver(FakeResolutionContext(resolvers={"runs": resolver, "quick": _TwoPlaylists()})))


def test_items_resolve_away_from_the_gui_thread_and_are_pending_until_then(
    qtbot: QtBot, blocking: _BlockingResolver
) -> None:
    store = _background_store(blocking)
    runs = PlaylistItem(label="Runs", resolver="runs")
    resolved: list[bool] = []
    store.items_resolved.connect(lambda _resolutions, added: resolved.append(added))

    store.add_items([runs])

    assert store.workspace.items == (runs,)
    assert store.is_modified
    assert store.resolution(runs.id).is_pending
    assert store.contents() == ()
    blocking.release.set()
    qtbot.waitUntil(lambda: resolved == [True])
    assert blocking.threads != [threading.main_thread()]
    assert [content.display_name for content in store.contents()] == ["Runs / train", "Runs / test"]


def test_a_result_follows_a_rename_made_while_the_item_resolved(qtbot: QtBot, blocking: _BlockingResolver) -> None:
    store = _background_store(blocking)
    runs = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([runs])

    store.rename_item(runs.id, "Exp 3")
    blocking.release.set()

    qtbot.waitUntil(lambda: not store.resolution(runs.id).is_pending)
    assert [content.display_name for content in store.contents()] == ["Exp 3 / train", "Exp 3 / test"]


def test_results_for_removed_items_or_a_replaced_workspace_are_dropped(
    qtbot: QtBot, blocking: _BlockingResolver
) -> None:
    store = _background_store(blocking)
    removed, replaced = PlaylistItem(resolver="runs"), PlaylistItem(resolver="runs")
    kept = PlaylistItem(label="Kept", resolver="quick")
    store.add_items([removed])
    store.add_items([replaced])
    resolved: list[list[ItemResolution]] = []
    store.items_resolved.connect(lambda resolutions, _added: resolved.append(resolutions))

    store.remove_item(removed.id)
    store.replace_workspace(Workspace(items=(kept,)))
    blocking.release.set()

    qtbot.waitUntil(lambda: len(resolved) == 1)
    assert [resolution.item for resolution in resolved[0]] == [kept]
    assert store.workspace.items == (kept,)
    assert [content.display_name for content in store.contents()] == ["Kept / train", "Kept / test"]


def test_a_replacing_workspace_resolves_without_waiting_for_the_one_it_replaced(
    qtbot: QtBot, blocking: _BlockingResolver
) -> None:
    store = _background_store(blocking)
    store.replace_workspace(Workspace(items=(PlaylistItem(resolver="runs"),)))
    requested = PlaylistItem(label="Requested", resolver="quick")

    store.replace_workspace(Workspace(items=(requested,)))

    qtbot.waitUntil(lambda: not store.resolution(requested.id).is_pending, timeout=2000)
    assert not blocking.release.is_set()
    assert [content.display_name for content in store.contents()] == ["Requested / train", "Requested / test"]


def test_items_of_an_opened_workspace_do_not_wait_for_a_slow_one(qtbot: QtBot, blocking: _BlockingResolver) -> None:
    store = _background_store(blocking)
    slow, quick = PlaylistItem(resolver="runs"), PlaylistItem(label="Quick", resolver="quick")

    store.replace_workspace(Workspace(items=(slow, quick)))

    qtbot.waitUntil(lambda: not store.resolution(quick.id).is_pending, timeout=2000)
    assert store.resolution(slow.id).is_pending


def test_queued_items_of_a_replaced_workspace_are_never_resolved(qtbot: QtBot, blocking: _BlockingResolver) -> None:
    counting = _CountingResolver()
    resolver = FakeResolutionContext(resolvers={"runs": blocking, "counted": counting, "quick": _TwoPlaylists()})
    store = WorkspaceStore(ItemResolver(resolver))
    busy = tuple(PlaylistItem(resolver="runs") for _ in range(8))  # More than the resolver has workers.
    store.replace_workspace(Workspace(items=(*busy, PlaylistItem(resolver="counted"))))
    requested = PlaylistItem(resolver="quick")

    store.replace_workspace(Workspace(items=(requested,)))
    blocking.release.set()

    qtbot.waitUntil(lambda: not store.resolution(requested.id).is_pending)
    assert counting.calls == 0


def test_replacing_a_workspace_stops_a_running_added_batch_before_its_next_item(
    qtbot: QtBot, blocking: _BlockingResolver
) -> None:
    counting = _CountingResolver()
    jobs = BackgroundJobs(max_workers=1)
    context = FakeResolutionContext(resolvers={"runs": blocking, "counted": counting, "quick": _TwoPlaylists()})
    store = WorkspaceStore(ItemResolver(context, jobs=jobs))
    store.add_items([PlaylistItem(resolver="runs"), PlaylistItem(resolver="counted")])
    qtbot.waitUntil(lambda: bool(blocking.threads))
    requested = PlaylistItem(label="Requested", resolver="quick")

    store.replace_workspace(Workspace(items=(requested,)))
    blocking.release.set()

    qtbot.waitUntil(lambda: not store.resolution(requested.id).is_pending)
    assert counting.calls == 0
    assert [content.display_name for content in store.contents()] == ["Requested / train", "Requested / test"]


def _jobs_with_a_busy_worker() -> tuple[BackgroundJobs, threading.Event, QObject]:
    """Return a one-worker pool whose only worker is held until the returned event is set."""
    jobs, busy, owner = BackgroundJobs(max_workers=1), threading.Event(), QObject()
    jobs.scope(owner).submit("busy", lambda _job: busy.wait(5), on_result=lambda _: None, on_error=lambda _: None)
    return jobs, busy, owner


@pytest.mark.parametrize("batched", [False, True], ids=["added-separately", "added-in-one-batch"])
def test_removing_an_item_skips_only_its_queued_resolution(qtbot: QtBot, batched: bool) -> None:
    resolver = _CountingResolver()
    baseline = set(threading.enumerate())
    jobs, busy, _owner = _jobs_with_a_busy_worker()
    store = WorkspaceStore(ItemResolver(FakeResolutionContext(resolvers={"runs": resolver}), jobs=jobs))
    removed, kept = PlaylistItem(resolver="runs"), PlaylistItem(resolver="runs")
    if batched:
        store.add_items([removed, kept])
    else:
        store.add_items([removed])
        store.add_items([kept])

    store.remove_item(removed.id)
    busy.set()

    _wait_for_jobs_since(qtbot, baseline)
    assert resolver.calls == 1 and not store.resolution(kept.id).is_pending


def _recording_bookmark_signals(store: WorkspaceStore) -> list[str]:
    signals: list[str] = []
    store.bookmarks_changed.connect(lambda: signals.append("bookmarks"))
    store.state_changed.connect(lambda: signals.append("state"))
    return signals


def test_each_bookmark_edit_signals_once_and_marks_the_workspace_modified(tmp_path: Path) -> None:
    store = _store()
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
    store = _store()
    runs = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([runs])
    target = BookmarkTarget(runs.id, "b.mp4")
    bookmark = store.add_bookmark(target, 12, 0.5, note="Gate")
    store.set_reviewed(target, True)
    path = tmp_path / "w.ax-devil.workspace"

    store.save_workspace(path)
    reopened = _store()
    reopened.open_workspace(path)

    assert reopened.bookmarks == (bookmark,)
    assert reopened.reviewed == {target}
    assert not reopened.is_modified


def test_requests_that_cannot_apply_change_nothing_and_signal_nothing() -> None:
    store = _store()
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
    store = _store()
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
    store = _store()
    runs = PlaylistItem(label="Runs", resolver="runs")
    bookmark = Bookmark(target=BookmarkTarget(runs.id, "a.mp4"), frame=1, time_s=0.0)
    path = tmp_path / "w.ax-devil.workspace"
    save_workspace(Workspace(items=(runs,), bookmarks=(bookmark,)), path)
    changes: list[tuple[Bookmark, ...]] = []
    store.bookmarks_changed.connect(lambda: changes.append(store.bookmarks))

    store.open_workspace(path)
    store.replace_workspace(Workspace())

    assert changes == [(bookmark,), ()]


class _Folder(FakeResolver):
    """A resolver over a folder the test changes: one playlist with an entry per clip, or an error once it is empty.

    Clips named ``hidden…`` are left out by default; each has one overlay file.
    """

    def __init__(self, *clips: str) -> None:
        self.clips = list(clips)
        self.root = Path("/runs")

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        if not self.clips:
            raise OSError("Folder not found: /runs")
        entries = tuple(
            PlaylistEntry(
                make_video(clip, overlay_count=1).standalone_lanes(), default_considered=not clip.startswith("hidden")
            )
            for clip in self.clips
        )
        return [PlaylistContent(display_name="Runs", entries=entries)]

    def source_paths(self, settings: PlaylistSettings, token: CancelToken) -> SourcePaths:
        return SourcePaths(folders=frozenset({self.root}))


def _entry_names(store: WorkspaceStore) -> list[str]:
    playlists = [cast(PlaylistContent, content) for content in store.contents()]
    return [entry.display_label(index) for playlist in playlists for index, entry in enumerate(playlist.entries)]


def _folder_store(folder: _Folder) -> tuple[WorkspaceStore, FakeFileWatcher]:
    watcher = FakeFileWatcher()
    store = WorkspaceStore(inline_resolver(FakeResolutionContext(resolvers={"folder": folder})), watcher=watcher)
    return store, watcher


def test_a_change_resolves_the_item_again_and_keeps_exclusions_only_of_what_still_exists(tmp_path: Path) -> None:
    folder = _Folder("a.mp4", "b.mp4", "c.mp4")
    store, watcher = _folder_store(folder)
    runs = PlaylistItem(label="Runs", resolver="folder")
    store.add_items([runs])
    store.save_workspace(tmp_path / "runs.ax-devil.workspace")
    playlist = _playlist(store)

    def considered(index: int) -> bool:
        return store.is_item_considered(_playlist(store).entry_consideration_ref(index))

    store.set_item_considered(playlist.entry_consideration_ref(0), False)
    store.set_item_considered(playlist.entry_consideration_ref(2), False)
    changes: list[frozenset[Path]] = []
    state_changes: list[None] = []
    store.sources_changed.connect(changes.append)
    store.state_changed.connect(lambda: state_changes.append(None))

    folder.clips = ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "hidden.mp4"]
    watcher.report(runs.id, Path("/runs/d.mp4"), Path("/runs/hidden.mp4"))
    assert _entry_names(store) == ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "hidden.mp4"]
    assert [considered(index) for index in range(5)] == [False, True, False, True, False]

    folder.clips = ["a.mp4", "b.mp4"]
    watcher.report(runs.id, Path("/runs/c.mp4"))
    assert _entry_names(store) == ["a.mp4", "b.mp4"]
    folder.clips = ["a.mp4", "b.mp4", "c.mp4"]
    folder.root = Path("/moved")
    watcher.report(runs.id, Path("/runs/c.mp4"))
    assert [considered(index) for index in range(3)] == [False, True, True]
    assert watcher.watched[runs.id].paths.folders == {Path("/runs"), Path("/moved")}, (
        "an item keeps watching what an earlier result was read from, which a viewer may still show"
    )

    assert changes == [
        frozenset({Path("/runs/d.mp4"), Path("/runs/hidden.mp4")}),
        frozenset({Path("/runs/c.mp4")}),
        frozenset({Path("/runs/c.mp4")}),
    ]
    assert [content.display_name for content in store.contents()] == ["Runs"]
    assert not store.is_modified and state_changes == []


def test_a_change_that_inserts_an_entry_before_an_excluded_one_keeps_that_entry_excluded() -> None:
    folder = _Folder("b.mp4", "c.mp4")
    store, watcher = _folder_store(folder)
    runs = PlaylistItem(label="Runs", resolver="folder")
    store.add_items([runs])
    playlist = _playlist(store)
    store.set_item_considered(playlist.entry_consideration_ref(0), False)

    folder.clips = ["a.mp4", "b.mp4", "c.mp4"]
    watcher.report(runs.id, Path("/runs/a.mp4"))

    changed = _playlist(store)
    considered = {
        entry.display_label(index): store.is_item_considered(changed.entry_consideration_ref(index))
        for index, entry in enumerate(changed.entries)
    }
    assert considered == {"a.mp4": True, "b.mp4": False, "c.mp4": True}


def test_an_open_viewer_and_the_sidebar_exclude_the_same_entries_after_entries_are_inserted() -> None:
    folder = _Folder("b.mp4", "c.mp4", "d.mp4")
    store, watcher = _folder_store(folder)
    runs = PlaylistItem(label="Runs", resolver="folder")
    store.add_items([runs])
    old = _playlist(store)  # What a viewer opened on b.mp4 keeps.

    folder.clips = ["a.mp4", "b.mp4", "c.mp4", "d.mp4"]
    watcher.report(runs.id, Path("/runs/a.mp4"))
    new = _playlist(store)
    store.set_item_considered(new.entry_consideration_ref(2), False)  # The sidebar hides c.mp4.

    assert OfflineViewerNavigation(old, store).find_next_considered_index(0) == 2, "the viewer steps to d.mp4"

    store.set_item_considered(old.entry_consideration_ref(0), False)  # The viewer hides b.mp4.

    assert [store.is_item_considered(new.entry_consideration_ref(index)) for index in range(3)] == [True, False, False]


def test_a_change_that_makes_resolving_fail_shows_the_item_as_unavailable_until_it_resolves_again() -> None:
    folder = _Folder("a.mp4")
    store, watcher = _folder_store(folder)
    runs = PlaylistItem(label="Runs", resolver="folder")
    store.add_items([runs])

    folder.clips = []
    watcher.report(runs.id, Path("/runs/a.mp4"))
    error = store.resolution(runs.id).error
    assert error is not None and "Folder not found" in str(error)
    assert store.contents() == ()
    watched = store.resolution(runs.id).sources.paths
    assert Path("/runs") in watched.folders and Path("/tmp/Overlay 1.txt") in watched.files, (
        "an item that cannot resolve keeps what it watched, so restoring those files helps"
    )
    assert watcher.watched[runs.id].paths == watched

    folder.clips = ["b.mp4"]
    watcher.report(runs.id, Path("/runs/b.mp4"))
    assert store.resolution(runs.id).error is None
    assert _entry_names(store) == ["b.mp4"]


def test_results_after_a_change_for_a_removed_item_or_a_replaced_workspace_are_dropped(
    qtbot: QtBot, blocking: _BlockingResolver
) -> None:
    baseline = set(threading.enumerate())
    resolver = ItemResolver(
        FakeResolutionContext(resolvers={"runs": blocking, "quick": _TwoPlaylists()}), jobs=BackgroundJobs()
    )
    watcher = FakeFileWatcher()
    store = WorkspaceStore(resolver, watcher=watcher)
    resolved_at_once = FakeResolutionContext(resolvers={"runs": _TwoPlaylists()})
    removed, replaced = PlaylistItem(resolver="runs"), PlaylistItem(resolver="runs")
    store.add_resolved(
        [ItemResolution.of(removed, resolved_at_once, NEVER), ItemResolution.of(replaced, resolved_at_once, NEVER)]
    )
    delivered: list[list[ItemResolution]] = []
    store.items_resolved.connect(lambda results, _added: delivered.append(results))
    watcher.report(removed.id)
    watcher.report(replaced.id)
    qtbot.waitUntil(lambda: len(blocking.threads) == 2)

    store.remove_item(removed.id)
    kept = PlaylistItem(id=replaced.id, label="Kept", resolver="quick")
    store.replace_workspace(Workspace(items=(kept,)))
    blocking.release.set()

    _wait_for_jobs_since(qtbot, baseline)
    assert [result.item for results in delivered for result in results] == [kept], "only the new Workspace's own result"
    assert [content.display_name for content in store.contents()] == ["Kept / train", "Kept / test"]


class _ScriptedResolver(FakeResolver):
    """Resolve once at once, then hold each later request until the test finishes it with its own playlist name."""

    def __init__(self) -> None:
        self.requests: list[tuple[threading.Event, str]] = []
        self._lock = threading.Lock()
        self._first = True

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        with self._lock:
            if self._first:
                self._first = False
                return [make_playlist("initial")]
            release, name = threading.Event(), f"scan {len(self.requests) + 1}"
            self.requests.append((release, name))
        assert release.wait(timeout=5)
        return [make_playlist(name)]


def test_an_older_resolution_finishing_last_does_not_overwrite_the_newer_result(qtbot: QtBot) -> None:
    scripted = _ScriptedResolver()
    baseline = set(threading.enumerate())
    resolver = ItemResolver(FakeResolutionContext(resolvers={"runs": scripted}), jobs=BackgroundJobs())
    watcher = FakeFileWatcher()
    store = WorkspaceStore(resolver, watcher=watcher)
    runs = PlaylistItem(resolver="runs")
    store.add_items([runs])
    qtbot.waitUntil(lambda: not store.resolution(runs.id).is_pending)
    delivered: list[list[ItemResolution]] = []
    store.items_resolved.connect(lambda results, _added: delivered.append(results))

    watcher.report(runs.id)
    qtbot.waitUntil(lambda: len(scripted.requests) == 1)
    watcher.report(runs.id)
    qtbot.waitUntil(lambda: len(scripted.requests) == 2)
    (older_release, _), (newer_release, newer_name) = scripted.requests
    newer_release.set()
    qtbot.waitUntil(lambda: len(delivered) == 1)
    older_release.set()
    _wait_for_jobs_since(qtbot, baseline)

    assert [content.display_name for content in store.contents()] == [newer_name]
    assert len(delivered) == 1


def test_repeated_changes_waiting_for_a_worker_resolve_only_once(qtbot: QtBot) -> None:
    scripted = _ScriptedResolver()
    baseline = set(threading.enumerate())
    jobs = BackgroundJobs(max_workers=1)
    resolver = ItemResolver(FakeResolutionContext(resolvers={"runs": scripted}), jobs=jobs)
    watcher = FakeFileWatcher()
    store = WorkspaceStore(resolver, watcher=watcher)
    runs = PlaylistItem(resolver="runs")
    store.add_items([runs])
    qtbot.waitUntil(lambda: not store.resolution(runs.id).is_pending)
    busy, owner = threading.Event(), QObject()
    jobs.scope(owner).submit("busy", lambda _job: busy.wait(5), on_result=lambda _: None, on_error=lambda _: None)

    for _ in range(3):
        watcher.report(runs.id)
    busy.set()
    qtbot.waitUntil(lambda: len(scripted.requests) == 1)
    scripted.requests[0][0].set()
    _wait_for_jobs_since(qtbot, baseline)

    assert len(scripted.requests) == 1, "the two resolutions the latest replaced never took a worker"


def _wait_for_jobs_since(qtbot: QtBot, baseline: set[threading.Thread]) -> None:
    """Wait until the workers started after *baseline* ran out of jobs, then let the GUI thread take every outcome."""
    qtbot.waitUntil(lambda: not set(threading.enumerate()) - baseline)
    QCoreApplication.processEvents()


class _CooperativeResolver(FakeResolver):
    """Hold one call until its owning workspace operation cancels it."""

    def __init__(self, block_call: int = 1) -> None:
        self.calls = 0
        self.block_call = block_call
        self.started = threading.Event()
        self.stopped = threading.Event()

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        """Return a numbered playlist, blocking the requested call with cooperative cancellation."""
        self.calls += 1
        if self.calls == self.block_call:
            self.started.set()
            try:
                token.wait(5)
            finally:
                self.stopped.set()
        return [make_playlist(f"scan {self.calls}")]


def test_removing_an_active_item_frees_the_worker_for_the_rest_of_its_added_batch(qtbot: QtBot) -> None:
    """Removing one slow playlist must stop its scan even while the batch still contains another item."""
    blocked = _CooperativeResolver()
    context = FakeResolutionContext(resolvers={"blocked": blocked, "quick": _TwoPlaylists()})
    store = WorkspaceStore(ItemResolver(context, jobs=BackgroundJobs(max_workers=1)))
    removed, kept = PlaylistItem(resolver="blocked"), PlaylistItem(resolver="quick")
    deliveries: list[list[ItemResolution]] = []
    store.items_resolved.connect(lambda results, _added: deliveries.append(results))
    try:
        store.add_items([removed, kept])
        qtbot.waitUntil(blocked.started.is_set)

        store.remove_item(removed.id)

        qtbot.waitUntil(lambda: not store.resolution(kept.id).is_pending, timeout=2000)
        assert blocked.stopped.is_set()
        assert [[result.item for result in results] for results in deliveries] == [[kept]]
        assert store.workspace.items == (kept,)
    finally:
        store.replace_workspace(Workspace())
        assert blocked.stopped.wait(5)


def test_added_items_publish_once_in_input_order_when_later_items_finish_first(
    qtbot: QtBot, blocking: _BlockingResolver
) -> None:
    counted = _CountingResolver()
    context = FakeResolutionContext(resolvers={"slow": blocking, "quick": counted})
    store = WorkspaceStore(ItemResolver(context, jobs=BackgroundJobs(max_workers=2)))
    slow, quick = PlaylistItem(resolver="slow"), PlaylistItem(resolver="quick")
    deliveries: list[list[ItemResolution]] = []
    store.items_resolved.connect(lambda results, _added: deliveries.append(results))
    store.add_items([slow, quick])
    qtbot.waitUntil(lambda: counted.calls == 1)
    assert deliveries == []

    blocking.release.set()

    qtbot.waitUntil(lambda: bool(deliveries))
    assert [[result.item for result in results] for results in deliveries] == [[slow, quick]]


def test_superseding_an_active_resolution_interrupts_it_before_the_next_one(qtbot: QtBot) -> None:
    """One workspace worker is enough for a replacement because its predecessor polls the job token."""
    resolver = _CooperativeResolver(block_call=2)
    context = FakeResolutionContext(resolvers={"runs": resolver})
    watcher = FakeFileWatcher()
    store = WorkspaceStore(ItemResolver(context, jobs=BackgroundJobs(max_workers=1)), watcher=watcher)
    runs = PlaylistItem(resolver="runs")
    delivered: list[list[ItemResolution]] = []
    try:
        store.add_items([runs])
        qtbot.waitUntil(lambda: not store.resolution(runs.id).is_pending)
        store.items_resolved.connect(lambda results, _added: delivered.append(results))
        watcher.report(runs.id)
        qtbot.waitUntil(resolver.started.is_set)

        watcher.report(runs.id)

        qtbot.waitUntil(lambda: bool(delivered), timeout=2000)
        assert resolver.stopped.is_set()
        assert [content.display_name for content in store.contents()] == ["scan 3"]
        assert len(delivered) == 1
    finally:
        store.replace_workspace(Workspace())
        assert resolver.stopped.wait(5)
