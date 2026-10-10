"""The Workspace store: items as the truth, their resolved Content, exclusions, and one signal per change."""

from __future__ import annotations

from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    Content,
    EntryLane,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    WorkspaceItem,
)
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_playlist, make_video
from tests.helpers.workspace import FakeResolutionContext, content_item


class _TwoPlaylists:
    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        return [make_playlist("train"), make_playlist("test")]


def _store() -> WorkspaceStore:
    return WorkspaceStore(FakeResolutionContext(resolvers={"runs": _TwoPlaylists()}))


def test_adding_items_resolves_them_and_emits_one_signal_after_the_state_is_updated() -> None:
    store = _store()
    first, second = content_item(make_video("first.mp4")), content_item(make_video("second.mp4"))
    batches: list[list[WorkspaceItem]] = []

    def record_added(items: list[WorkspaceItem]) -> None:
        assert [content.display_name for content in store.contents()] == ["first.mp4", "second.mp4"]
        batches.append(items)

    store.items_added.connect(record_added)

    store.add_items([first, second])

    assert batches == [[first, second]]
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
