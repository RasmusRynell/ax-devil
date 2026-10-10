"""The Workspace value: naming, edits that return new values, and equality as the modified test."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import PlaylistItem, VideoItem, Workspace


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
