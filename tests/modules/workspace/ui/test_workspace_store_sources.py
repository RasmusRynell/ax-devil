"""The Workspace store resolves an item again when what it was read from changes."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import cast

from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import NEVER, CancelToken
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.video_viewer.offline_viewer_navigation import OfflineViewerNavigation
from ax_devil.modules.workspace.core import (
    ItemResolution,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    Workspace,
)
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_playlist, make_video
from tests.helpers.workspace import FakeFileWatcher, FakeResolutionContext, FakeResolver, inline_resolver
from tests.helpers.workspace_store import BlockingResolver, TwoPlaylists, playlist_at, wait_for_jobs_since


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
    playlist = playlist_at(store)

    def considered(index: int) -> bool:
        return store.is_item_considered(playlist_at(store).entry_consideration_ref(index))

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
    playlist = playlist_at(store)
    store.set_item_considered(playlist.entry_consideration_ref(0), False)

    folder.clips = ["a.mp4", "b.mp4", "c.mp4"]
    watcher.report(runs.id, Path("/runs/a.mp4"))

    changed = playlist_at(store)
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
    old = playlist_at(store)  # What a viewer opened on b.mp4 keeps.

    folder.clips = ["a.mp4", "b.mp4", "c.mp4", "d.mp4"]
    watcher.report(runs.id, Path("/runs/a.mp4"))
    new = playlist_at(store)
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
    qtbot: QtBot, blocking: BlockingResolver
) -> None:
    baseline = set(threading.enumerate())
    resolver = ItemResolver(
        FakeResolutionContext(resolvers={"runs": blocking, "quick": TwoPlaylists()}), jobs=BackgroundJobs()
    )
    watcher = FakeFileWatcher()
    store = WorkspaceStore(resolver, watcher=watcher)
    resolved_at_once = FakeResolutionContext(resolvers={"runs": TwoPlaylists()})
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

    wait_for_jobs_since(qtbot, baseline)
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
    wait_for_jobs_since(qtbot, baseline)

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
    wait_for_jobs_since(qtbot, baseline)

    assert len(scripted.requests) == 1, "the two resolutions the latest replaced never took a worker"
