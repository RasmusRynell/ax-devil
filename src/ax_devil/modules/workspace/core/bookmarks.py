"""Bookmarks: frames a user marked while reviewing, and the targets they mark as reviewed."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from ax_devil.modules.workspace.core.content import PlaylistContent
from ax_devil.modules.workspace.core.items import _read, new_item_id

_EntryT = TypeVar("_EntryT")


@dataclass(frozen=True, order=True)
class BookmarkTarget:
    """One video that is reviewed: a Video Item (``entry`` empty) or one entry of a Playlist Item.

    A playlist entry is named by its playlist's name and its own ``display_label`` (see ``entry_target``), since entries
    have no id and their indexes shift when the resolver runs again. A target whose item or entry no longer exists is
    orphaned and kept; only removing its item removes it.
    """

    item_id: str
    entry: str = ""

    def to_json(self) -> dict[str, Any]:
        """Return the target as the JSON fields ``item`` and ``entry``."""
        return {"item": self.item_id, "entry": self.entry}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> BookmarkTarget:
        """Return the target in *data*'s ``item`` and ``entry``; raise ``ValueError`` saying what is malformed."""
        return cls(_read(data, "item", str), _read(data, "entry", str, ""))


def entry_target(item_id: str, playlist: PlaylistContent, entry_index: int) -> BookmarkTarget:
    """Return the target for entry *entry_index* of *playlist*, which a Playlist Item *item_id* resolved to.

    One Playlist Item may resolve to several playlists, so the entry is named "<playlist name>/<entry label>".
    """
    label = playlist.entries[entry_index].display_label(entry_index)
    return BookmarkTarget(item_id, f"{playlist.display_name}/{label}")


def find_entry(playlists: Sequence[PlaylistContent], target: BookmarkTarget) -> tuple[PlaylistContent, int] | None:
    """Return the playlist and entry index *target* names among *playlists*, or None when it no longer exists.

    *playlists* are the Content of the target's item. Entries that share a name resolve to the first of them.
    """
    for playlist in playlists:
        for index in range(len(playlist.entries)):
            if entry_target(target.item_id, playlist, index) == target:
                return playlist, index
    return None


class BookmarkKind(Enum):
    """What a bookmark marks; saved by its value."""

    NOTE = "note"
    ISSUE = "issue"
    EXAMPLE = "example"
    QUESTION = "question"

    @property
    def label(self) -> str:
        """Return the kind's name as shown to the user."""
        return self.value.capitalize()

    @classmethod
    def from_value(cls, value: str) -> BookmarkKind:
        """Return the kind saved as *value*, or raise ``ValueError``."""
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"unknown bookmark kind {value!r}") from None


@dataclass(frozen=True, kw_only=True)
class Bookmark:
    """A frame of a target's video the user marked, with what kind of moment it is and an optional note.

    ``frame`` is the frame index in the target's video; ``time_s`` is that frame's time, kept for display.
    """

    target: BookmarkTarget
    frame: int
    time_s: float
    kind: BookmarkKind = BookmarkKind.NOTE
    note: str = ""
    id: str = field(default_factory=new_item_id)

    def __post_init__(self) -> None:
        """Reject a negative frame or a negative or non-finite time."""
        if self.frame < 0:
            raise ValueError(f"A bookmark frame cannot be negative: {self.frame}")
        if not (math.isfinite(self.time_s) and self.time_s >= 0):
            raise ValueError(f"A bookmark time must be a finite, non-negative number: {self.time_s}")

    @property
    def label(self) -> str:
        """Return the note, or the frame when there is none."""
        return self.note or f"Frame {self.frame}"

    def to_json(self) -> dict[str, Any]:
        """Return every field as JSON; the kind is its value."""
        return {
            "id": self.id,
            **self.target.to_json(),
            "frame": self.frame,
            "time": self.time_s,
            "kind": self.kind.value,
            "note": self.note,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Bookmark:
        """Return the bookmark *data* describes; raise ``ValueError`` saying what is malformed.

        ``entry``, ``kind`` and ``note`` may be absent and take their defaults.
        """
        time_s = data.get("time")
        if isinstance(time_s, bool) or not isinstance(time_s, (int, float)):
            raise ValueError("'time' must be a number")
        return cls(
            id=_read(data, "id", str),
            target=BookmarkTarget.from_json(data),
            frame=_read(data, "frame", int),
            time_s=float(time_s),
            kind=BookmarkKind.from_value(_read(data, "kind", str, BookmarkKind.NOTE.value)),
            note=_read(data, "note", str, ""),
        )


def bookmarks_to_json(bookmarks: Iterable[Bookmark], reviewed: Iterable[BookmarkTarget]) -> dict[str, Any]:
    """Return the ``bookmarks`` and ``reviewed`` lists of a saved Workspace; reviewed targets are sorted."""
    return {
        "bookmarks": [bookmark.to_json() for bookmark in bookmarks],
        "reviewed": [target.to_json() for target in sorted(reviewed)],
    }


def bookmarks_from_json(document: Mapping[str, Any]) -> tuple[tuple[Bookmark, ...], frozenset[BookmarkTarget]]:
    """Return the bookmarks and reviewed targets in a saved Workspace; absent lists are empty.

    Raises ``ValueError`` naming the first malformed entry, so a damaged file is reported instead of losing bookmarks.
    """
    bookmarks = _read_entries(document, "bookmarks", "bookmark", Bookmark.from_json)
    reviewed = _read_entries(document, "reviewed", "reviewed target", BookmarkTarget.from_json)
    return bookmarks, frozenset(reviewed)


def _read_entries(
    document: Mapping[str, Any], key: str, name: str, read: Callable[[Mapping[str, Any]], _EntryT]
) -> tuple[_EntryT, ...]:
    """Return every object in the list ``document[key]`` read by *read*; none when the list is absent.

    Raises ``ValueError`` naming the first malformed entry by *name* and its position, counted from 1.
    """
    entries = []
    for index, entry in enumerate(_read(document, key, list, []), start=1):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{name} {index} is not a JSON object")
        try:
            entries.append(read(entry))
        except ValueError as exc:
            raise ValueError(f"{name} {index} is malformed: {exc}") from exc
    return tuple(entries)
