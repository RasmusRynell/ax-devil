"""The rows of the Bookmarks view: one group per bookmarked target, its bookmarks under it, filtered and searched."""

from __future__ import annotations

from collections.abc import Sequence, Set
from dataclasses import dataclass, replace

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import KindColor, StatusColor
from ax_devil.modules.video_player.ui.controls import format_timecode
from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkKind,
    BookmarkTarget,
    Content,
    ItemResolution,
    entry_name,
    find_entry,
    target_name,
)
from ax_devil.modules.workspace.core.bookmark_csv import BookmarkCsvRow

_KIND_MARKS: dict[BookmarkKind, KindColor | StatusColor] = {
    BookmarkKind.NOTE: KindColor.MUTED,
    BookmarkKind.ISSUE: KindColor.LIVE,
    BookmarkKind.EXAMPLE: StatusColor.SUCCESS,
    BookmarkKind.QUESTION: KindColor.INFO,
}
"""The color each kind of bookmark is marked in."""

MISSING = "Missing"
"""What a bookmark of a target that no longer exists says instead of its position."""


@dataclass(frozen=True)
class BookmarkRow:
    """One bookmark as its row shows it."""

    bookmark: Bookmark
    subtitle: str

    @property
    def mark(self) -> KindColor | StatusColor:
        """Return the color the bookmark's icon is drawn in, by its kind."""
        return _KIND_MARKS[self.bookmark.kind]


@dataclass(frozen=True)
class BookmarkGroup:
    """A bookmarked target, where its video is now, and its bookmarks in frame order.

    *found* is the Content and entry index the target names now, or None when it is orphaned: its entry is gone or its
    item did not resolve. An orphaned group's bookmarks do not open and its review state cannot change.
    """

    target: BookmarkTarget
    item_name: str
    reviewed: bool
    found: tuple[Content, int] | None
    rows: tuple[BookmarkRow, ...]

    @property
    def title(self) -> str:
        """Return the target's name, as ``target_name`` gives it."""
        return target_name(self.item_name, self.target)

    @property
    def icon(self) -> Icon:
        """Return the playlist icon for a playlist entry, and the video icon for a Video Item."""
        return Icon.PLAYLIST if self.target.entry else Icon.VIDEO

    @property
    def mark(self) -> KindColor:
        """Return the color of the icon: the playlist or the video color."""
        return KindColor.PLAYLIST if self.target.entry else KindColor.VIDEO

    @property
    def missing(self) -> bool:
        """Return whether the target is orphaned."""
        return self.found is None

    def csv_rows(self) -> tuple[BookmarkCsvRow, ...]:
        """Return this group's bookmarks as CSV rows."""
        entry = entry_name(self.item_name, self.target)
        return tuple(
            BookmarkCsvRow(
                self.item_name,
                entry,
                row.bookmark.frame,
                row.bookmark.time_s,
                row.bookmark.kind.label,
                row.bookmark.note,
            )
            for row in self.rows
        )


def bookmark_subtitle(bookmark: Bookmark) -> str:
    """Return what a bookmark's row says under its label: its time, frame, and kind."""
    return f"{format_timecode(bookmark.time_s)} · frame {bookmark.frame} · {bookmark.kind.label}"


def build_bookmark_groups(
    resolutions: Sequence[ItemResolution], bookmarks: Sequence[Bookmark], reviewed: Set[BookmarkTarget]
) -> tuple[BookmarkGroup, ...]:
    """Return a group per target with bookmarks, in item order, then entry order with orphaned entries last.

    *resolutions* are every item's, in Workspace order. Only bookmarkable items are listed, so live streams never are.
    """
    by_target: dict[BookmarkTarget, list[Bookmark]] = {}
    for bookmark in bookmarks:
        by_target.setdefault(bookmark.target, []).append(bookmark)
    groups: list[BookmarkGroup] = []
    for resolution in resolutions:
        item = resolution.item
        if not item.bookmarkable:
            continue
        entries = [entry for content in resolution.contents for entry in content.bookmark_entries]
        order = {entry: position for position, entry in reversed(list(enumerate(entries)))}
        targets = sorted(
            (target for target in by_target if target.item_id == item.id),
            key=lambda target: (order.get(target.entry, len(entries)), target.entry),
        )
        for target in targets:
            found = find_entry(resolution.contents, target)
            marks = sorted(by_target[target], key=lambda bookmark: (bookmark.frame, bookmark.time_s))
            rows = tuple(
                BookmarkRow(bookmark, MISSING if found is None else bookmark_subtitle(bookmark)) for bookmark in marks
            )
            groups.append(BookmarkGroup(target, item.display_name, target in reviewed, found, rows))
    return tuple(groups)


def filter_bookmark_groups(
    groups: Sequence[BookmarkGroup], query: str, kind: BookmarkKind | None
) -> tuple[BookmarkGroup, ...]:
    """Return *groups* with only the bookmarks of *kind* (any when None) whose label or group title holds *query*.

    The search ignores case and surrounding spaces; groups left without bookmarks are dropped.
    """
    needle = query.strip().casefold()
    filtered = []
    for group in groups:
        title_matches = needle in group.title.casefold()
        rows = tuple(
            row
            for row in group.rows
            if (kind is None or row.bookmark.kind is kind)
            and (title_matches or needle in row.bookmark.label.casefold())
        )
        if rows:
            filtered.append(replace(group, rows=rows))
    return tuple(filtered)
