"""The Workspace value: naming, edits that return new values, bookmarks, and equality as the modified test."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkKind,
    BookmarkTarget,
    LiveStreamItem,
    PlaylistItem,
    UnreadableItem,
    VideoItem,
    Workspace,
    WorkspaceItem,
)


def _video(name: str) -> VideoItem:
    return VideoItem(label=name, video=Path(f"/clips/{name}"))


def test_workspace_is_untitled_until_saved_and_then_named_after_its_file() -> None:
    assert Workspace().name == "Untitled"
    assert Workspace(path=Path("/work/parking lot.ax-devil.workspace")).name == "parking lot"


def test_edits_return_new_workspaces_and_leave_the_original_unchanged() -> None:
    first, second, third = _video("a.mp4"), _video("b.mp4"), _video("c.mp4")
    original = Workspace().add_items([first, second])

    added = original.add_items([third])
    removed = added.remove_item(second.id)
    renamed = removed.rename_item(third.id, "Gate")

    assert original.items == (first, second)
    assert added.items == (first, second, third)
    assert removed.items == (first, third)
    assert [(item.id, item.label) for item in renamed.items] == [(first.id, "a.mp4"), (third.id, "Gate")]


def test_editing_an_item_that_is_not_in_the_workspace_raises() -> None:
    workspace = Workspace().add_items([_video("a.mp4")])

    with pytest.raises(KeyError):
        workspace.remove_item("missing")
    with pytest.raises(KeyError):
        workspace.rename_item("missing", "Gate")


def test_a_workspace_cannot_hold_two_items_with_one_id() -> None:
    item = _video("a.mp4")

    with pytest.raises(ValueError, match="unique ids"):
        Workspace().add_items([item, item])


def test_workspaces_with_equal_items_are_equal_so_reverting_an_edit_is_unmodified() -> None:
    """Modified is ``current != saved``; it must hold for items with settings mappings too."""
    playlist = PlaylistItem(label="Runs", resolver="folder_pair", settings={"videos_dir": "/runs", "tags": ["a"]})
    saved = Workspace(path=Path("/work/a.ax-devil.workspace"), items=(playlist,))
    video = _video("b.mp4")

    edited = saved.add_items([video])
    reverted = edited.remove_item(video.id)

    assert edited != saved
    assert reverted == saved
    assert saved.rename_item(playlist.id, "Runs") == saved
    assert saved.rename_item(playlist.id, "Other") != saved
    assert Workspace(items=(playlist,)) != saved


def test_bookmark_edits_return_new_workspaces_and_reviewing_marks_one_target() -> None:
    video, playlist = _video("a.mp4"), PlaylistItem(label="Runs", resolver="folder_pair")
    clip, entry = BookmarkTarget(video.id), BookmarkTarget(playlist.id, "b.mp4")
    first = Bookmark(target=clip, frame=30, time_s=1.0)
    second = Bookmark(target=entry, frame=5, time_s=0.2, kind=BookmarkKind.ISSUE, note="Missed box")
    original = Workspace().add_items([video, playlist])

    bookmarked = original.add_bookmark(first).add_bookmark(second)
    edited = bookmarked.edit_bookmark(first.id, note="Occluded", kind=BookmarkKind.EXAMPLE)
    removed = edited.remove_bookmark(second.id)
    reviewed = removed.set_reviewed(entry, True)

    assert original.bookmarks == ()
    assert bookmarked.bookmarks == (first, second)
    assert [(b.note, b.kind, b.frame) for b in edited.bookmarks] == [
        ("Occluded", BookmarkKind.EXAMPLE, 30),
        ("Missed box", BookmarkKind.ISSUE, 5),
    ]
    assert [b.id for b in removed.bookmarks] == [first.id]
    assert reviewed.reviewed == {entry}
    assert reviewed.set_reviewed(entry, False).reviewed == frozenset()


def test_editing_only_the_note_or_the_kind_keeps_the_other() -> None:
    video = _video("a.mp4")
    bookmark = Bookmark(target=BookmarkTarget(video.id), frame=1, time_s=0.0, kind=BookmarkKind.QUESTION, note="Why?")
    workspace = Workspace(items=(video,), bookmarks=(bookmark,))

    assert workspace.edit_bookmark(bookmark.id, note="").bookmark(bookmark.id).kind == BookmarkKind.QUESTION
    assert workspace.edit_bookmark(bookmark.id, kind=BookmarkKind.NOTE).bookmark(bookmark.id).note == "Why?"


@pytest.mark.parametrize(
    "item",
    [
        LiveStreamItem(label="Entrance", host="camera"),
        UnreadableItem.from_raw({"kind": "hologram", "id": "h1"}, "Unknown item kind"),
    ],
)
def test_live_streams_and_unreadable_items_take_no_new_bookmarks_or_review_marks(item: WorkspaceItem) -> None:
    workspace = Workspace(items=(item,))
    target = BookmarkTarget(item.id)

    with pytest.raises(ValueError, match="cannot take bookmarks"):
        workspace.add_bookmark(Bookmark(target=target, frame=0, time_s=0.0))
    with pytest.raises(ValueError, match="cannot take bookmarks"):
        workspace.set_reviewed(target, True)


def test_bookmarks_and_review_marks_need_an_item_in_the_workspace_and_unique_ids() -> None:
    video = _video("a.mp4")
    workspace = Workspace(items=(video,))
    bookmark = Bookmark(target=BookmarkTarget(video.id), frame=0, time_s=0.0)
    elsewhere = BookmarkTarget("missing")

    with pytest.raises(ValueError):
        workspace.add_bookmark(Bookmark(target=elsewhere, frame=0, time_s=0.0))
    with pytest.raises(ValueError):
        workspace.set_reviewed(elsewhere, True)
    with pytest.raises(ValueError, match="unique ids"):
        workspace.add_bookmark(bookmark).add_bookmark(bookmark)
    with pytest.raises(ValueError, match="not in the workspace"):
        Workspace(items=(video,), reviewed=frozenset({elsewhere}))
    with pytest.raises(KeyError):
        workspace.remove_bookmark("missing")
    with pytest.raises(KeyError):
        workspace.edit_bookmark("missing", note="x")


def test_removing_an_item_removes_its_bookmarks_and_review_marks_only() -> None:
    gone, kept = _video("a.mp4"), PlaylistItem(label="Runs", resolver="folder_pair")
    gone_mark = Bookmark(target=BookmarkTarget(gone.id), frame=3, time_s=0.1)
    kept_mark = Bookmark(target=BookmarkTarget(kept.id, "b.mp4"), frame=4, time_s=0.2)
    workspace = Workspace(
        items=(gone, kept),
        bookmarks=(gone_mark, kept_mark),
        reviewed=frozenset({BookmarkTarget(gone.id), BookmarkTarget(kept.id, "b.mp4")}),
    )

    removed = workspace.remove_item(gone.id)

    assert removed.bookmarks == (kept_mark,)
    assert removed.reviewed == {BookmarkTarget(kept.id, "b.mp4")}


def test_bookmark_and_review_edits_make_a_workspace_modified_until_reverted() -> None:
    video = _video("a.mp4")
    target = BookmarkTarget(video.id)
    saved = Workspace(path=Path("/work/a.ax-devil.workspace"), items=(video,))
    bookmark = Bookmark(target=target, frame=12, time_s=0.5)

    bookmarked = saved.add_bookmark(bookmark)

    assert bookmarked != saved
    assert bookmarked.remove_bookmark(bookmark.id) == saved
    assert bookmarked.edit_bookmark(bookmark.id, note="Gate") != bookmarked
    assert bookmarked.edit_bookmark(bookmark.id, note="") == bookmarked
    assert saved.set_reviewed(target, True) != saved
    assert saved.set_reviewed(target, True).set_reviewed(target, False) == saved
