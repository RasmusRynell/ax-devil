"""Mutable workspace state, notifications, and content-browser row projection."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence, Set
from dataclasses import dataclass, replace
from functools import partial
from pathlib import PurePath
from typing import Callable, Literal

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.content import (
    ConsiderationItemRef,
    Content,
    LiveVideoContent,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.item_info import (
    WorkspaceItemInfo,
    build_content_information,
    build_playlist_entry_information,
    build_playlist_lane_information,
    build_video_lane_information,
)

WorkspaceBrowserIconKind = Literal["video", "live_video", "playlist", "overlay"]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OnScreenWorkspaceItem:
    """One item currently shown by a workspace viewer."""

    kind: Literal["video", "playlist_entry"]
    content_id: str
    entry_index: int | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceBrowserRow:
    """Explicit row data rendered by the content browser widget."""

    row_id: str
    label: str
    icon_kind: WorkspaceBrowserIconKind
    activation_target: tuple[Content, int] | None = None
    consideration_ref: ConsiderationItemRef | None = None
    removable_content: Content | None = None
    information_factory: Callable[[], WorkspaceItemInfo] | None = None
    export_target: OnScreenWorkspaceItem | None = None
    is_considered: bool = True
    is_open: bool = False
    children: tuple[WorkspaceBrowserRow, ...] = ()
    location: str | None = None
    location_hint: str = ""

    @property
    def display_text(self) -> str:
        """Return the label, followed by a location hint when a sibling shares the label."""
        return f"{self.label} — {self.location_hint}" if self.location_hint else self.label

    @property
    def tooltip(self) -> str:
        """Return the full location and open state shown when hovering the row."""
        lines = [self.location or "", "Open in at least one viewer" if self.is_open else ""]
        return "\n".join(line for line in lines if line)


def _distinguishing_hints(locations: Sequence[str]) -> list[str]:
    """Return a hint per location; repeated locations are numbered in order of appearance."""
    distinct = list(dict.fromkeys(locations))
    hint_by_location = dict(zip(distinct, _shortest_distinct_tails(distinct)))
    if len(distinct) == len(locations):
        return [hint_by_location[location] for location in locations]
    totals = Counter(locations)
    seen: Counter[str] = Counter()
    hints = []
    for location in locations:
        seen[location] += 1
        number = f" ({seen[location]})" if totals[location] > 1 else ""
        hints.append(f"{hint_by_location[location]}{number}".strip())
    return hints


def _shortest_distinct_tails(locations: Sequence[str]) -> list[str]:
    """Return the shortest trailing folder path, or else source path, that tells distinct locations apart."""
    if len(locations) == 1:
        return [PurePath(locations[0]).parent.name]
    folders = [PurePath(location).parent.parts for location in locations]
    sources = [PurePath(location).parts for location in locations]
    for parts_list in (folders, sources):
        for depth in range(1, max(len(parts) for parts in parts_list) + 1):
            tails = [str(PurePath(*parts[-depth:])) if parts else "" for parts in parts_list]
            if len(set(tails)) == len(tails):
                return tails
    return list(locations)


def _with_location_hints(rows: tuple[WorkspaceBrowserRow, ...]) -> tuple[WorkspaceBrowserRow, ...]:
    """Add location hints to sibling rows that share a label, recursing into children.

    Rows without a location, or with the same location, are told apart by occurrence number.
    """
    same_label: defaultdict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        same_label[row.label].append(index)
    hints: dict[int, str] = {}
    for indexes in same_label.values():
        if len(indexes) > 1:
            locations = [rows[index].location or "" for index in indexes]
            hints.update(zip(indexes, _distinguishing_hints(locations)))
    return tuple(
        replace(row, location_hint=hints.get(index, ""), children=_with_location_hints(row.children))
        for index, row in enumerate(rows)
    )


class WorkspaceManager(QObject):
    """Own workspace content, consideration state, browser rows, and mutation signals."""

    contents_added = Signal(list)
    content_removed = Signal(object)
    workspace_cleared = Signal()
    item_consideration_changed = Signal(object, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._contents: list[Content] = []
        self._not_considered: set[ConsiderationItemRef] = set()

    def add_content(self, content: Content) -> None:
        """Append *content* and emit one addition event."""
        self.add_contents([content])

    def add_contents(self, contents: Sequence[Content]) -> None:
        """Append multiple content items and emit one batch addition event."""
        if not contents:
            return
        added_contents = list(contents)
        for content in added_contents:
            self._contents.append(content)
            self._seed_consideration_defaults(content)
            logger.debug(f"Content added: {content.content_id}")
        self.contents_added.emit(added_contents)

    def remove_content(self, content: Content) -> None:
        """Remove *content* and emit ``content_removed`` when it existed."""
        try:
            self._contents.remove(content)
        except ValueError:
            logger.warning(f"Attempted to remove content not in workspace: {content.content_id}")
            return
        self._discard_refs_for_content(content.content_id)
        logger.debug(f"Content removed: {content.content_id}")
        self.content_removed.emit(content)

    def clear(self) -> None:
        """Remove all workspace facts."""
        self._contents.clear()
        self._not_considered.clear()
        logger.debug("Workspace cleared")
        self.workspace_cleared.emit()

    def get_contents(self) -> list[Content]:
        """Return a shallow copy of the workspace contents."""
        return list(self._contents)

    def is_item_considered(self, item_ref: ConsiderationItemRef) -> bool:
        """Return whether the referenced item participates in navigation/layout."""
        return item_ref not in self._not_considered

    def set_item_considered(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Update consideration state and emit a change signal when it changes."""
        if not self._contains_consideration_ref(item_ref):
            logger.warning(f"Ignoring consideration change for unknown Workspace item: {item_ref}")
            return
        if self.is_item_considered(item_ref) == considered:
            return
        if considered:
            self._not_considered.discard(item_ref)
        else:
            self._not_considered.add(item_ref)
        logger.debug(f"Item consideration changed: {item_ref} considered={considered}")
        self.item_consideration_changed.emit(item_ref, considered)

    def get_browser_rows(
        self,
        open_items: Set[OnScreenWorkspaceItem] = frozenset(),
    ) -> tuple[WorkspaceBrowserRow, ...]:
        """Return derived rows for the content browser."""
        return _with_location_hints(tuple(self._content_row(content, open_items) for content in self._contents))

    def _content_row(self, content: Content, open_items: Set[OnScreenWorkspaceItem]) -> WorkspaceBrowserRow:
        """Build a top-level row for one content item."""
        if isinstance(content, PlaylistContent):
            children = tuple(
                self._playlist_entry_row(content, entry, entry_index, open_items)
                for entry_index, entry in enumerate(content.entries)
            )
            return WorkspaceBrowserRow(
                row_id=content.content_id,
                label=content.display_name,
                icon_kind="playlist",
                activation_target=(content, 0),
                removable_content=content,
                information_factory=partial(build_content_information, content),
                is_open=self._is_content_open(content.content_id, open_items),
                children=children,
                location=content.source_location,
            )

        return WorkspaceBrowserRow(
            row_id=content.content_id,
            label=content.display_name,
            icon_kind=self._icon_kind_for_video(content),
            activation_target=(content, 0),
            removable_content=content,
            information_factory=partial(build_content_information, content),
            export_target=(
                OnScreenWorkspaceItem(kind="video", content_id=content.content_id)
                if isinstance(content, SeekableVideoContent)
                else None
            ),
            is_open=self._is_content_open(content.content_id, open_items),
            children=self._video_lane_rows(content),
            location=content.source_location,
        )

    def _playlist_entry_row(
        self,
        playlist: PlaylistContent,
        entry: PlaylistEntry,
        entry_index: int,
        open_items: Set[OnScreenWorkspaceItem],
    ) -> WorkspaceBrowserRow:
        """Build a row for one playlist entry."""
        open_item = OnScreenWorkspaceItem(
            kind="playlist_entry",
            content_id=playlist.content_id,
            entry_index=entry_index,
        )
        children: tuple[WorkspaceBrowserRow, ...] = ()
        if entry.should_show_lane_children():
            children = tuple(
                self._playlist_lane_row(playlist, entry, entry_index, lane_index)
                for lane_index in range(len(entry.lanes))
            )
        return WorkspaceBrowserRow(
            row_id=f"{playlist.content_id}/entry/{entry_index}",
            label=entry.display_label(entry_index),
            icon_kind=self._icon_kind_for_playlist_entry(entry),
            activation_target=(playlist, entry_index),
            consideration_ref=ConsiderationItemRef.playlist_entry(playlist.content_id, entry_index),
            information_factory=partial(build_playlist_entry_information, playlist, entry, entry_index),
            export_target=open_item,
            is_considered=self.is_item_considered(
                ConsiderationItemRef.playlist_entry(playlist.content_id, entry_index)
            ),
            is_open=open_item in open_items,
            children=children,
            location=entry.source_location,
        )

    def _video_lane_rows(self, video: SeekableVideoContent | LiveVideoContent) -> tuple[WorkspaceBrowserRow, ...]:
        """Build child rows for visible standalone video lanes."""
        consideration_items = {item.ref: item for item in video.consideration_items()}
        rows: list[WorkspaceBrowserRow] = []
        for lane_index, lane in enumerate(video.standalone_lanes()):
            if lane.source_kind == OverlaySourceKind.NO_SOURCE:
                continue
            lane_ref = ConsiderationItemRef.video_lane(video.content_id, lane_index)
            consideration_item = consideration_items.get(lane_ref)
            rows.append(
                WorkspaceBrowserRow(
                    row_id=f"{video.content_id}/lane/{lane_index}",
                    label=lane.display_name,
                    icon_kind="overlay",
                    activation_target=(video, 0),
                    consideration_ref=lane_ref if consideration_item is not None else None,
                    information_factory=partial(build_video_lane_information, video, lane),
                    is_considered=(self.is_item_considered(lane_ref) if consideration_item is not None else True),
                    location=lane.source_location,
                )
            )
        return tuple(rows)

    def _playlist_lane_row(
        self,
        playlist: PlaylistContent,
        entry: PlaylistEntry,
        entry_index: int,
        lane_index: int,
    ) -> WorkspaceBrowserRow:
        """Build a row for one playlist lane."""
        lane = entry.lanes[lane_index]
        return WorkspaceBrowserRow(
            row_id=f"{playlist.content_id}/entry/{entry_index}/lane/{lane_index}",
            label=lane.display_name,
            icon_kind="overlay",
            activation_target=(playlist, entry_index),
            consideration_ref=ConsiderationItemRef.playlist_lane(playlist.content_id, entry_index, lane_index),
            information_factory=partial(
                build_playlist_lane_information,
                playlist,
                entry,
                entry_index,
                lane,
                lane_index,
            ),
            is_considered=self.is_item_considered(
                ConsiderationItemRef.playlist_lane(playlist.content_id, entry_index, lane_index)
            ),
            location=lane.source_location,
        )

    def _icon_kind_for_video(self, video: SeekableVideoContent | LiveVideoContent) -> WorkspaceBrowserIconKind:
        """Return the browser icon kind for a video row."""
        return "live_video" if isinstance(video, LiveVideoContent) else "video"

    def _icon_kind_for_playlist_entry(self, entry: PlaylistEntry) -> WorkspaceBrowserIconKind:
        """Return the browser icon kind for a playlist entry row."""
        unique_videos = {lane.video for lane in entry.lanes}
        if len(unique_videos) == 1:
            video = next(iter(unique_videos))
            return self._icon_kind_for_video(video)
        return "playlist"

    def _is_content_open(self, content_id: str, open_items: Set[OnScreenWorkspaceItem]) -> bool:
        """Return whether any open item belongs to the given content."""
        return any(item.content_id == content_id for item in open_items)

    def _seed_consideration_defaults(self, content: Content) -> None:
        """Populate initial not-considered refs from content default flags."""
        self._discard_refs_for_content(content.content_id)
        self._not_considered.update(item.ref for item in content.consideration_items() if not item.default_considered)

    def _contains_consideration_ref(self, item_ref: ConsiderationItemRef) -> bool:
        """Return whether *item_ref* identifies a current Workspace item."""
        return any(
            any(item.ref == item_ref for item in content.consideration_items())
            for content in self._contents
            if content.content_id == item_ref.content_id
        )

    def _discard_refs_for_content(self, content_id: str) -> None:
        """Remove all consideration refs belonging to the given content."""
        self._not_considered = {ref for ref in self._not_considered if ref.content_id != content_id}
