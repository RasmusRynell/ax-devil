"""The Workspace store: items as the truth, their resolved Content, exclusions, and one signal per change."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import (
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
from tests.helpers.workspace import FakeResolutionContext, content_item, inline_resolver


class _TwoPlaylists:
    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        return [make_playlist("train"), make_playlist("test")]


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
    excluded = ConsiderationItemRef.playlist_entry(store.contents()[1].content_id, 0)
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
    lane = ConsiderationItemRef.video_lane(before.content_id, 1)
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
    [playlist] = store.contents()
    entry = ConsiderationItemRef.playlist_entry(playlist.content_id, 0)
    hidden_lane = ConsiderationItemRef.playlist_lane(playlist.content_id, 1, 1)
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
    [playlist] = store.contents()
    changes: list[object] = []
    store.item_consideration_changed.connect(lambda *args: changes.append(args))

    for ref in (
        ConsiderationItemRef.video_lane("nonexistent", 0),
        ConsiderationItemRef.playlist_lane(playlist.content_id, 0, 99),
    ):
        store.set_item_considered(ref, False)
        assert store.is_item_considered(ref)
    assert changes == []


class _CountingResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
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
    save_workspace(Workspace(items=items), path)
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


def test_saving_names_the_workspace_and_clears_modified(tmp_path: Path) -> None:
    store = _store()
    item = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([item])
    assert store.is_modified
    path = tmp_path / "mine.ax-devil.workspace"

    store.save_workspace(path)

    assert store.workspace.path == path and store.workspace.name == "mine"
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
    store.save_workspace(tmp_path / "w.ax-devil.workspace")
    store.rename_item(first.id, "A2")
    store.remove_item(second.id)
    store.save_workspace()
    store.open_workspace(tmp_path / "w.ax-devil.workspace")

    assert states == [(True, "Untitled"), (False, "w"), (True, "w"), (False, "w")]


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


class _BlockingResolver:
    """Resolve to two playlists once the test releases it, recording the thread it ran on."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.threads: list[threading.Thread] = []

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
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
