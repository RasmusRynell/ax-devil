"""Bookmark groups follow the workspace's item and entry order and never list live streams."""

from __future__ import annotations

from pathlib import Path

from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkTarget,
    FileVideoSourceSpec,
    ItemResolution,
    LiveStreamItem,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    SeekableVideoContent,
    VideoItem,
)
from ax_devil.modules.workspace.ui.bookmark_rows import build_bookmark_groups


def _video(name: str) -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=Path(name)))


def _bookmark(target: BookmarkTarget, frame: int = 0) -> Bookmark:
    return Bookmark(target=target, frame=frame, time_s=frame / 10)


def test_groups_follow_item_then_entry_order_with_orphaned_entries_last() -> None:
    video = VideoItem(video=Path("clip.mp4"))
    playlist = PlaylistItem(resolver="runs", label="Runs")
    runs = PlaylistContent(
        display_name="runs",
        entries=tuple(
            PlaylistEntry(lanes=_video(name).standalone_lanes(), default_considered=True) for name in ("a.mp4", "b.mp4")
        ),
    )
    resolutions = (
        ItemResolution(playlist, by_default_name=(runs,)),
        ItemResolution(video, by_default_name=(_video("clip.mp4"),)),
    )
    bookmarks = (
        _bookmark(BookmarkTarget(video.id)),
        _bookmark(BookmarkTarget(playlist.id, "runs/gone.mp4")),
        _bookmark(BookmarkTarget(playlist.id, "runs/b.mp4")),
        _bookmark(BookmarkTarget(playlist.id, "runs/a.mp4")),
    )

    groups = build_bookmark_groups(resolutions, bookmarks, {BookmarkTarget(video.id)})

    assert [(group.title, group.missing, group.reviewed) for group in groups] == [
        ("Runs › a.mp4", False, False),
        ("Runs › b.mp4", False, False),
        ("Runs › runs/gone.mp4", True, False),
        ("clip.mp4", False, True),
    ]
    assert groups[1].found is not None and groups[1].found[1] == 1


def test_an_unresolved_item_leaves_its_bookmarks_missing_and_live_streams_are_never_listed() -> None:
    video = VideoItem(video=Path("clip.mp4"))
    live = LiveStreamItem(host="192.0.2.10")
    resolutions = (ItemResolution.pending(video), ItemResolution.pending(live))

    groups = build_bookmark_groups(
        resolutions, (_bookmark(BookmarkTarget(video.id), 5), _bookmark(BookmarkTarget(live.id))), set()
    )

    assert [(group.title, group.missing) for group in groups] == [("clip.mp4", True)]
    assert [row.subtitle for row in groups[0].rows] == ["Missing"]
