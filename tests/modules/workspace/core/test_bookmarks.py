"""Bookmarks: what a valid bookmark is and how it is shown."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import Bookmark, BookmarkKind, BookmarkTarget, entry_target, find_entry
from ax_devil.modules.workspace.core.content import (
    FileVideoSourceSpec,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)


def _playlist(name: str, *videos: str) -> PlaylistContent:
    entries = tuple(
        PlaylistEntry(
            lanes=SeekableVideoContent(
                display_name=video, source_spec=FileVideoSourceSpec(Path(video))
            ).standalone_lanes(),
            default_considered=True,
        )
        for video in videos
    )
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

    assert entry_target("item", train, 0) != entry_target("item", test, 0)
    assert find_entry([train, test], entry_target("item", test, 0)) == (test, 0)
    assert find_entry([train, test], entry_target("item", train, 1)) == (train, 1)


def test_an_entry_that_no_longer_exists_is_not_found() -> None:
    old = entry_target("item", _playlist("train", "gone.mp4"), 0)

    assert find_entry([_playlist("train", "a.mp4")], old) is None
