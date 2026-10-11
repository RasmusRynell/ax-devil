"""Bookmarks: what a valid bookmark is and how it is shown."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkKind,
    BookmarkTarget,
    ItemResolution,
    PlaylistItem,
    entry_target,
    find_entry,
)
from ax_devil.modules.workspace.core.content import (
    FileVideoSourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
    numbered_when_shared,
)


def _video(name: str) -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(Path(name)))


def _playlist(name: str, *videos: str) -> PlaylistContent:
    entries = tuple(PlaylistEntry(lanes=_video(video).standalone_lanes(), default_considered=True) for video in videos)
    return PlaylistContent(display_name=name, entries=entries)


def test_a_bookmark_is_shown_by_its_note_or_else_its_frame() -> None:
    target = BookmarkTarget("item")

    assert Bookmark(target=target, frame=30, time_s=1.0).label == "Frame 30"
    assert Bookmark(target=target, frame=30, time_s=1.0, note="Missed person").label == "Missed person"


def test_new_bookmarks_are_notes_with_distinct_ids() -> None:
    first = Bookmark(target=BookmarkTarget("item"), frame=0, time_s=0.0)
    second = Bookmark(target=BookmarkTarget("item"), frame=0, time_s=0.0)

    assert first.kind == BookmarkKind.NOTE
    assert first.id and first.id != second.id


@pytest.mark.parametrize(("frame", "time_s"), [(-1, 0.0), (0, -0.5), (0, float("nan")), (0, float("inf"))])
def test_a_bookmark_needs_a_non_negative_frame_and_a_finite_non_negative_time(frame: int, time_s: float) -> None:
    with pytest.raises(ValueError):
        Bookmark(target=BookmarkTarget("item"), frame=frame, time_s=time_s)


def test_kinds_are_read_from_their_saved_values_and_unknown_values_are_rejected() -> None:
    assert [BookmarkKind.from_value(kind.value) for kind in BookmarkKind] == list(BookmarkKind)
    with pytest.raises(ValueError, match="sticker"):
        BookmarkKind.from_value("sticker")


def test_entries_of_two_playlists_of_one_item_are_told_apart_even_when_they_share_a_video_name() -> None:
    train = _playlist("train", "a.mp4", "b.mp4")
    test = _playlist("test", "a.mp4")

    assert entry_target(train, 0) != entry_target(test, 0)
    assert find_entry([train, test], entry_target(test, 0)) == (test, 0)
    assert find_entry([train, test], entry_target(train, 1)) == (train, 1)


def test_an_entry_that_no_longer_exists_is_not_found() -> None:
    old = entry_target(_playlist("train", "gone.mp4"), 0)

    assert find_entry([_playlist("train", "a.mp4")], old) is None


def test_a_video_is_its_own_target_and_live_video_has_none() -> None:
    video = replace(_video("clip.mp4"), item_id="item")
    live = LiveVideoContent(display_name="cam", source_spec=LiveRTSPStreamSpec(host="cam", username="", password=""))

    assert entry_target(video, 0) == BookmarkTarget("item")
    assert find_entry([live, video], BookmarkTarget("item")) == (video, 0)


def test_renaming_an_item_keeps_the_keys_of_its_playlist_entries() -> None:
    item = PlaylistItem(resolver="runs")
    runs = replace(_playlist("runs", "a.mp4", "b.mp4"), item_id=item.id, content_id=f"{item.id}/0")
    resolution = ItemResolution(item, by_default_name=(runs,))
    target = entry_target(resolution.contents[0], 1)

    renamed = resolution.renamed(item.with_label("My run"))

    assert renamed.contents[0].display_name == "My run"
    assert find_entry(renamed.contents, target) == (renamed.contents[0], 1)
    assert entry_target(renamed.contents[0], 1) == target


def test_entries_that_share_a_label_get_numbered_keys_so_each_has_its_own_target() -> None:
    runs = _playlist("runs", "run1/video.mp4", "run2/video.mp4")
    runs = replace(
        runs,
        entries=tuple(
            PlaylistEntry(lanes=_video("video.mp4").standalone_lanes(), default_considered=True) for _ in range(2)
        ),
    )

    assert runs.bookmark_entries == ("runs/video.mp4", "runs/video.mp4 (2)")
    assert find_entry([runs], entry_target(runs, 1)) == (runs, 1)


def test_numbering_shared_labels_never_repeats_a_label_the_resolver_already_gave() -> None:
    assert numbered_when_shared(["video.mp4", "video.mp4", "video.mp4 (2)"]) == (
        "video.mp4",
        "video.mp4 (3)",
        "video.mp4 (2)",
    )
