"""Bookmarks: what a valid bookmark is and how it is shown."""

from __future__ import annotations

import pytest

from ax_devil.modules.workspace.core import Bookmark, BookmarkKind, BookmarkTarget


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
