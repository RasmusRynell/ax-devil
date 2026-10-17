"""The rows of the Bookmarks view: one group per bookmarked or reviewed target, filtered and searched."""

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
    find_entry,
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
    """A bookmarked or reviewed target, where its video is now, and its bookmarks in frame order, possibly none.

    *found* is the Content and entry index the target names now, or None when it is orphaned or its item is resolving.
    *missing* is the orphaned case: the item resolved, or failed to, without the entry. A missing group's bookmarks do
    not open and its review mark can only be cleared; while the item resolves the group is neither missing nor openable.
    """

    target: BookmarkTarget
    item_name: str
    entry: str
    """The entry as shown under its item: empty for a Video Item, else its title, with its playlist's name only when
    the item resolved to several playlists or the entry is gone."""
    reviewed: bool
    found: tuple[Content, int] | None
    missing: bool
    rows: tuple[BookmarkRow, ...]

    @property
    def title(self) -> str:
        """Return the target's name: the item's, then the entry's for a playlist entry."""
        return f"{self.item_name} › {self.entry}" if self.entry else self.item_name

    @property
    def icon(self) -> Icon:
        """Return the playlist icon for a playlist entry, and the video icon for a Video Item."""
        return Icon.PLAYLIST if self.target.entry else Icon.VIDEO

    @property
    def mark(self) -> KindColor:
        """Return the color of the icon: the playlist or the video color."""
        return KindColor.PLAYLIST if self.target.entry else KindColor.VIDEO

    def csv_rows(self) -> tuple[BookmarkCsvRow, ...]:
        """Return this group's bookmarks as CSV rows."""
        return tuple(
            BookmarkCsvRow(
                self.item_name,
                self.entry,
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
    """Return a group per target with bookmarks or a review mark, in item order, then entry order with orphaned last.

    *resolutions* are every item's, in Workspace order. Every stored bookmark and review mark is listed, whatever its
    item, so ones kept for an item this version cannot read stay visible and exportable; items without any are not.
    """
    by_target: dict[BookmarkTarget, list[Bookmark]] = {target: [] for target in reviewed}
    for bookmark in bookmarks:
        by_target.setdefault(bookmark.target, []).append(bookmark)
    groups: list[BookmarkGroup] = []
    for resolution in resolutions:
        item = resolution.item
        entries = [entry for content in resolution.contents for entry in content.bookmark_entries]
        order = {entry: position for position, entry in reversed(list(enumerate(entries)))}
        targets = sorted(
            (target for target in by_target if target.item_id == item.id),
            key=lambda target: (order.get(target.entry, len(entries)), target.entry),
        )
        for target in targets:
            found = find_entry(resolution.contents, target)
            marks = sorted(by_target[target], key=lambda bookmark: (bookmark.frame, bookmark.time_s))
            missing = found is None and not resolution.is_pending
            rows = tuple(
                BookmarkRow(bookmark, MISSING if missing else bookmark_subtitle(bookmark)) for bookmark in marks
            )
            entry = _entry_shown(resolution.contents, target, found)
            groups.append(BookmarkGroup(target, item.display_name, entry, target in reviewed, found, missing, rows))
    return tuple(groups)


def _entry_shown(contents: Sequence[Content], target: BookmarkTarget, found: tuple[Content, int] | None) -> str:
    """Return how *target*'s entry is shown under its item: its title when the item has one playlist, else its key."""
    if found is None or len(contents) != 1:
        return target.entry
    content, index = found
    return content.bookmark_entry_titles[index]


def filter_bookmark_groups(
    groups: Sequence[BookmarkGroup], query: str, kind: BookmarkKind | None
) -> tuple[BookmarkGroup, ...]:
    """Return *groups* with only the bookmarks of *kind* (any when None) whose label or group title holds *query*.

    The search ignores case and surrounding spaces; groups left without bookmarks are dropped, except that a reviewed
    target without bookmarks stays while no kind is chosen and the search matches its title.
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
        if rows or (not group.rows and kind is None and title_matches):
            filtered.append(replace(group, rows=rows))
    return tuple(filtered)
