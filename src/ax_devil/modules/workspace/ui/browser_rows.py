"""Sidebar row projection over Workspace items, their Content, consideration state, and open items.

The rows also carry what every sidebar view shows of them, the actions a row offers and whether it is idle, so the
views render one look and one menu without deciding it again.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass, replace
from enum import Enum
from functools import partial
from pathlib import PurePath
from typing import Any, Literal

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.workspace.core.content import (
    ConsiderationItemRef,
    Content,
    EntryLane,
    LiveVideoContent,
    OnScreenWorkspaceItem,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.core.item_info import (
    WorkspaceItemInfo,
    build_playlist_entry_information,
    build_playlist_information,
    build_playlist_lane_information,
    build_unavailable_information,
    build_video_information,
    build_video_lane_information,
)
from ax_devil.modules.workspace.core.items import ItemResolution, LiveStreamItem, PlaylistItem, VideoItem, WorkspaceItem

WorkspaceBrowserIconKind = Literal["video", "live_video", "playlist", "overlay", "unavailable"]


class BrowserSection(Enum):
    """A titled group of top-level rows, in the order the browser lists them, with the icon kind it is marked by.

    An *addable* section lists things the Workspace does not hold yet, which its rows' Add adds; its mark is muted.
    A section listed without rows says its *empty_text* under its caption, or is hidden when it has none.
    """

    LIVE = ("Live", "live_video", False)
    VIDEOS = ("Videos", "video", False)
    PLAYLISTS = ("Playlists", "playlist", False)
    OTHER = ("Other", "unavailable", False)
    NETWORK = ("On the network", "live_video", True, "No new cameras")
    """Cameras a network scan found."""
    ADD_FROM = ("Add from", "playlist", True)
    """The playlist resolvers that can add playlists."""

    def __init__(self, title: str, icon_kind: WorkspaceBrowserIconKind, addable: bool, empty_text: str = "") -> None:
        self.title = title
        self.icon_kind = icon_kind
        self.addable = addable
        self.empty_text = empty_text


HELD_SECTIONS = frozenset(section for section in BrowserSection if not section.addable)
"""The sections of what the Workspace holds."""


@dataclass(frozen=True, slots=True)
class Facet:
    """Something the Filter can hide rows by: a *value* in a *group*, such as Network Speaker under Type."""

    group: str
    value: str


class RowActionGroup(Enum):
    """The set of related actions a row's menu keeps together, split from the next by a separator."""

    OPEN = "open"
    INSPECT = "inspect"
    VISIBILITY = "visibility"
    EDIT = "edit"


class RowAction(Enum):
    """Something the user can do with a row, in the order a row's menu lists it, grouped by *group*.

    Each action has its menu text and hover text; an *inline* one is also the button at the row's right, with its own
    button text and icon.
    """

    OPEN = ("Open", "", None, "Replace the preview (unpinned) pane", False, RowActionGroup.OPEN)
    OPEN_TO_SIDE = (
        "Open to the Side",
        "",
        None,
        "Open pinned in a new split (Ctrl+double-click)",
        False,
        RowActionGroup.OPEN,
    )
    START_STREAM = ("Start stream", "Start stream", None, "", True, RowActionGroup.OPEN)
    STOP_STREAM = ("Stop stream", "Stop stream", None, "", True, RowActionGroup.OPEN)
    ADD = ("Add", "Add", Icon.ADD, "", True, RowActionGroup.OPEN)
    INFORMATION = ("Information", "", None, "", False, RowActionGroup.INSPECT)
    EXPORT_VIDEO = ("Export video", "", None, "", False, RowActionGroup.INSPECT)
    HIDE = ("Hide", "", None, "Skip it in playback", False, RowActionGroup.VISIBILITY)
    UNHIDE = ("Unhide", "", None, "Play it again", False, RowActionGroup.VISIBILITY)
    RENAME = ("Rename", "", None, "", False, RowActionGroup.EDIT)
    REMOVE = ("Remove", "", None, "", False, RowActionGroup.EDIT)

    def __init__(
        self,
        menu_text: str,
        button_text: str,
        icon: Icon | None,
        tooltip: str,
        inline: bool,
        group: RowActionGroup,
    ) -> None:
        self.menu_text = menu_text
        self.button_text = button_text
        self.icon = icon
        self.tooltip = tooltip
        self.inline = inline
        self.group = group


@dataclass(frozen=True, slots=True)
class OfferedAction:
    """A *action* a row offers; with a *disabled_reason* it is listed but cannot be done, and the reason says why."""

    action: RowAction
    disabled_reason: str = ""


_SECTION_BY_ITEM_KIND = {
    LiveStreamItem.kind: BrowserSection.LIVE,
    VideoItem.kind: BrowserSection.VIDEOS,
    PlaylistItem.kind: BrowserSection.PLAYLISTS,
}
"""The section of each item kind; any other kind, such as an unreadable item, goes under Other."""


def _section_of(item: WorkspaceItem) -> BrowserSection:
    """Return the section that lists *item*'s rows."""
    return _SECTION_BY_ITEM_KIND.get(item.kind, BrowserSection.OTHER)


EXPANDED_AT_FIRST_LIMIT = 200
"""The most entries a playlist can have and still start expanded; building a row widget costs about a millisecond,
so a longer playlist stays collapsed rather than stall the sidebar while it loads."""


@dataclass(frozen=True, slots=True)
class WorkspaceBrowserRow:
    """Explicit row data rendered by every sidebar view's section tree.

    The projection resolves the final detail before publication; the second line, search, and accessibility use the
    same text. Hovering shows the row's facts, location, and state.
    """

    row_id: str
    label: str
    icon_kind: WorkspaceBrowserIconKind
    activation_target: tuple[Content, int] | None = None
    consideration_ref: ConsiderationItemRef | None = None
    item: WorkspaceItem | None = None
    """The item a top-level row stands for, which the row can remove or rename; None for nested rows."""
    unavailable_reason: str | None = None
    """Why the row's item could not open; such a row shows the reason instead of opening."""
    warning: str | None = None
    """A problem with the row's item that does not stop it from opening, shown on hover."""
    information_factory: Callable[[], WorkspaceItemInfo] | None = None
    export_target: OnScreenWorkspaceItem | None = None
    is_considered: bool = True
    is_open: bool = False
    children: tuple[WorkspaceBrowserRow, ...] = ()
    location: str | None = None
    detail: str = ""
    """The final muted text shown under the label; empty for none, which keeps the row one line high."""
    wrap_detail: bool = False
    """Whether the detail may take multiple lines instead of being elided."""
    facts: tuple[str, ...] = ()
    """Lines that open the hover text, starting with what kind of row this is."""
    actions: tuple[OfferedAction, ...] = ()
    """The actions the row offers, in menu order; the first inline one is the button at the row's right."""
    add: Callable[[], None] | None = None
    """What the Add action of a row for something the Workspace does not hold does: add it, or ask how."""
    idle: bool = False
    """Whether the row's thing is not running: a camera no viewer shows, or an item still loading."""
    search_terms: str = ""
    """More text the search matches beyond what the row shows, such as a found camera's serial number."""
    facets: frozenset[Facet] = frozenset()
    """What the Filter can hide the row by."""
    expanded_at_first: bool = False
    """Whether the row shows the rows under it until the user collapses it."""

    def offered(self, action: RowAction) -> OfferedAction | None:
        """Return *action* as the row offers it, or None when it does not."""
        return next((offered for offered in self.actions if offered.action is action), None)

    @property
    def inline_action(self) -> OfferedAction | None:
        """Return the action shown as a button on the row, if it offers one."""
        return next((offered for offered in self.actions if offered.action.inline), None)

    @property
    def is_available(self) -> bool:
        """Return whether the row stands for something the Workspace does not hold yet, which its Add adds."""
        return self.offered(RowAction.ADD) is not None

    @property
    def is_mark_muted(self) -> bool:
        """Return whether the row's kind mark is drawn muted instead of in its kind's color."""
        return self.idle or self.is_available

    @property
    def is_dimmed(self) -> bool:
        """Return whether the row's name is dimmed: it is idle, hidden from playback, or cannot open."""
        return self.idle or not self.is_considered or self.unavailable_reason is not None

    @property
    def search_text(self) -> str:
        """Return the text the search field matches: the label, detail and search terms."""
        return " ".join(part for part in (self.label, self.detail, self.search_terms) if part)

    @property
    def tooltip(self) -> str:
        """Return the hover text below the row's name: its facts, why it is unavailable, its location, whether
        it is hidden from playback, and whether it is open."""
        lines = [
            *self.facts,
            self.unavailable_reason or "",
            self.warning or "",
            self.location or "",
            "Hidden: skipped in playback" if not self.is_considered else "",
            "Open in a viewer" if self.is_open else "",
        ]
        return "\n".join(line for line in lines if line)


@dataclass(frozen=True, slots=True)
class WorkspaceBrowserSection:
    """One browser heading and the item rows it groups; the count comes from those rows."""

    kind: BrowserSection
    rows: tuple[WorkspaceBrowserRow, ...]


@dataclass(frozen=True, slots=True)
class RowOrder:
    """A named order for the rows of a view: rows sort by what *key* returns, and equal keys keep the order given."""

    label: str
    key: Callable[[WorkspaceBrowserRow], tuple[Any, ...]]

    def arrange(self, sections: Sequence[WorkspaceBrowserSection]) -> tuple[WorkspaceBrowserSection, ...]:
        """Return *sections* with the top-level rows of each in this order; the rows under a row keep theirs, so a
        playlist's entries stay in playlist order."""
        return tuple(replace(section, rows=tuple(sorted(section.rows, key=self.key))) for section in sections)


WORKSPACE_ORDER = RowOrder("Workspace order", lambda _row: ())
"""Rows as the workspace, or whatever feeds the view, lists them."""
BY_NAME = RowOrder("Name", lambda row: (row.label.casefold(),))


def _with_actions(row: WorkspaceBrowserRow, inline: RowAction | None = None) -> WorkspaceBrowserRow:
    """Return *row* offering what its fields allow, and *inline* as its button.

    A row that cannot open still offers Open, which says why. Export needs the row open in a viewer, Hide or Unhide
    is for a row playback can skip, and Rename needs an item that keeps a new name.
    """
    offered: list[OfferedAction] = []
    if row.activation_target is not None or row.unavailable_reason is not None:
        offered.append(OfferedAction(RowAction.OPEN))
    if row.activation_target is not None:
        offered.append(OfferedAction(RowAction.OPEN_TO_SIDE))
    if inline is not None:
        offered.append(OfferedAction(inline))
    if row.information_factory is not None:
        offered.append(OfferedAction(RowAction.INFORMATION))
    if row.export_target is not None:
        offered.append(OfferedAction(RowAction.EXPORT_VIDEO, "" if row.is_open else "Open it first"))
    if row.consideration_ref is not None:
        offered.append(OfferedAction(RowAction.HIDE if row.is_considered else RowAction.UNHIDE))
    if row.item is not None:
        offered.append(OfferedAction(RowAction.RENAME, "" if row.item.renamable else "This item cannot be renamed"))
        offered.append(OfferedAction(RowAction.REMOVE))
    return replace(row, actions=tuple(offered))


def _counted(count: int, noun: str, plural: str = "") -> str:
    """Return *count* followed by *noun*, or by *plural* (default *noun* plus s) unless the count is one."""
    return f"{count} {noun}" if count == 1 else f"{count} {plural or f'{noun}s'}"


def _lane_fact(lane: EntryLane) -> str:
    """Return what a lane row shows: an overlay, or the plain video."""
    return "Video" if lane.overlay is None else "Overlay"


def _distinguishing_hints(rows: Sequence[WorkspaceBrowserRow], file_paths: dict[str, PurePath]) -> list[str]:
    """Return a hint per location; repeated locations are numbered in order of appearance."""
    locations = [row.location or "" for row in rows]
    hint_by_location = {location: location for location in locations}
    files = list(dict.fromkeys(str(file_paths[row.row_id]) for row in rows if row.row_id in file_paths))
    if files:
        hint_by_location.update(zip(files, _shortest_distinct_tails(files)))
    distinct = list(dict.fromkeys(locations))
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


def _with_details(
    rows: tuple[WorkspaceBrowserRow, ...], file_paths: dict[str, PurePath]
) -> tuple[WorkspaceBrowserRow, ...]:
    """Add location hints to sibling rows that share a label, recursing into children.

    Rows without a location, or with the same location, are told apart by occurrence number.
    """
    same_label: defaultdict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        resolved = row.information_factory is not None and row.unavailable_reason is None
        if resolved:  # Rows still loading or failed have no location to tell apart.
            same_label[row.label].append(index)
    hints: dict[int, str] = {}
    for indexes in same_label.values():
        if len(indexes) > 1:
            hints.update(zip(indexes, _distinguishing_hints([rows[index] for index in indexes], file_paths)))
    return tuple(
        replace(row, detail=hints.get(index, row.detail), children=_with_details(row.children, file_paths))
        for index, row in enumerate(rows)
    )


class _RowProjection:
    """Build browser rows, asking *is_considered* for the consideration state of each referenced item."""

    def __init__(self, is_considered: Callable[[ConsiderationItemRef], bool]) -> None:
        self._is_considered = is_considered
        self._file_paths: dict[str, PurePath] = {}  # Only file rows participate in folder disambiguation.

    def sections(
        self, resolutions: Sequence[ItemResolution], open_items: Set[OnScreenWorkspaceItem]
    ) -> tuple[WorkspaceBrowserSection, ...]:
        """Return a section per kind of item present, holding a row per Content, or per unavailable or pending
        item, with hints for rows that look alike within a section."""
        rows = tuple(
            row
            for resolution in resolutions
            for row in (
                [self._unavailable_row(resolution.item, str(resolution.error))]
                if resolution.error is not None
                else [self._pending_row(resolution.item)]
                if resolution.is_pending
                else [self._content_row(resolution.item, content, open_items) for content in resolution.contents]
            )
        )
        by_section: dict[BrowserSection, list[WorkspaceBrowserRow]] = {
            section: [] for section in BrowserSection if not section.addable
        }
        for row in rows:
            assert row.item is not None  # Top-level rows stand for an item.
            by_section[_section_of(row.item)].append(row)
        return tuple(
            WorkspaceBrowserSection(kind=section, rows=_with_details(tuple(section_rows), self._file_paths))
            for section, section_rows in by_section.items()
            if section_rows
        )

    def _pending_row(self, item: WorkspaceItem) -> WorkspaceBrowserRow:
        """Build the single row of an item that is still resolving; it opens nothing until its Content arrives."""
        return _with_actions(
            WorkspaceBrowserRow(
                row_id=item.id,
                label=item.display_name,
                icon_kind=_section_of(item).icon_kind,
                item=item,
                idle=True,
            )
        )

    def _unavailable_row(self, item: WorkspaceItem, reason: str) -> WorkspaceBrowserRow:
        """Build the single row of an item that could not resolve."""
        return _with_actions(
            WorkspaceBrowserRow(
                row_id=item.id,
                label=item.display_name,
                icon_kind="unavailable",
                item=item,
                unavailable_reason=reason,
                information_factory=partial(build_unavailable_information, item.display_name, reason),
            )
        )

    def _content_row(
        self, item: WorkspaceItem, content: Content, open_items: Set[OnScreenWorkspaceItem]
    ) -> WorkspaceBrowserRow:
        """Build a top-level row for one Content of *item*."""
        if isinstance(content, PlaylistContent):
            children = tuple(
                self._playlist_entry_row(content, entry, entry_index, open_items)
                for entry_index, entry in enumerate(content.entries)
            )
            total = len(children)
            taking_part = sum(child.is_considered for child in children)
            entries = _counted(total, "entry", "entries")
            excluded = f", {total - taking_part} hidden" if taking_part < total else ""
            return _with_actions(
                WorkspaceBrowserRow(
                    row_id=content.content_id,
                    label=content.display_name,
                    icon_kind="playlist",
                    activation_target=(content, 0),
                    item=item,
                    information_factory=partial(build_playlist_information, content),
                    is_open=self._is_content_open(content.content_id, open_items),
                    children=children,
                    location=content.source_location,
                    facts=(f"Playlist · {entries}{excluded}",),
                    expanded_at_first=total <= EXPANDED_AT_FIRST_LIMIT,
                )
            )

        if not content.is_live:
            self._file_paths[content.content_id] = PurePath(content.source_location)
        overlays = f" · {_counted(len(content.overlays), 'overlay')}" if content.overlays else ""
        is_open = self._is_content_open(content.content_id, open_items)
        return _with_actions(
            WorkspaceBrowserRow(
                row_id=content.content_id,
                label=content.display_name,
                icon_kind=self._icon_kind_for_video(content),
                activation_target=None if content.is_live else (content, 0),
                item=item,
                information_factory=partial(build_video_information, content),
                export_target=None if content.is_live else content.on_screen_item(),
                is_open=is_open,
                children=self._video_lane_rows(content),
                location=content.source_location,
                detail=content.source_location
                if content.is_live and content.source_location != content.display_name
                else "",
                facts=(f"{'Live stream' if content.is_live else 'Video'}{overlays}",),
                idle=content.is_live and not is_open,
            ),
            inline=(RowAction.STOP_STREAM if is_open else RowAction.START_STREAM) if content.is_live else None,
        )

    def _playlist_entry_row(
        self,
        playlist: PlaylistContent,
        entry: PlaylistEntry,
        entry_index: int,
        open_items: Set[OnScreenWorkspaceItem],
    ) -> WorkspaceBrowserRow:
        """Build a row for one playlist entry."""
        open_item = playlist.on_screen_item(entry_index)
        children: tuple[WorkspaceBrowserRow, ...] = ()
        if entry.should_show_lane_children():
            children = tuple(
                self._playlist_lane_row(playlist, entry, entry_index, lane_index)
                for lane_index in range(len(entry.lanes))
            )
        row_id = f"{playlist.content_id}/entry/{entry_index}"
        if entry.source_location is not None:
            self._file_paths[row_id] = PurePath(entry.source_location)
        return _with_actions(
            WorkspaceBrowserRow(
                row_id=row_id,
                label=entry.display_label(entry_index),
                icon_kind=self._icon_kind_for_playlist_entry(entry),
                activation_target=(playlist, entry_index),
                consideration_ref=ConsiderationItemRef.playlist_entry(playlist.content_id, entry_index),
                information_factory=partial(build_playlist_entry_information, playlist, entry, entry_index),
                export_target=open_item,
                is_considered=self._is_considered(
                    ConsiderationItemRef.playlist_entry(playlist.content_id, entry_index)
                ),
                is_open=open_item in open_items,
                children=children,
                location=entry.source_location,
                facts=(f"Entry {entry_index + 1} of {len(playlist.entries)}",),
            )
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
            row_id = f"{video.content_id}/lane/{lane_index}"
            if not video.is_live:
                self._file_paths[row_id] = PurePath(lane.source_location)
            rows.append(
                _with_actions(
                    WorkspaceBrowserRow(
                        row_id=row_id,
                        label=lane.display_name,
                        icon_kind="overlay",
                        activation_target=None if video.is_live else (video, 0),
                        consideration_ref=lane_ref if consideration_item is not None else None,
                        information_factory=partial(build_video_lane_information, video, lane),
                        is_considered=(self._is_considered(lane_ref) if consideration_item is not None else True),
                        location=lane.source_location,
                        facts=(_lane_fact(lane),),
                    )
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
        row_id = f"{playlist.content_id}/entry/{entry_index}/lane/{lane_index}"
        self._file_paths[row_id] = PurePath(lane.source_location)
        return _with_actions(
            WorkspaceBrowserRow(
                row_id=row_id,
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
                is_considered=self._is_considered(
                    ConsiderationItemRef.playlist_lane(playlist.content_id, entry_index, lane_index)
                ),
                location=lane.source_location,
                facts=(_lane_fact(lane),),
            )
        )

    def _icon_kind_for_video(self, video: SeekableVideoContent | LiveVideoContent) -> WorkspaceBrowserIconKind:
        """Return the browser icon kind for a video row."""
        return "live_video" if video.is_live else "video"

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


def build_browser_sections(
    resolutions: Sequence[ItemResolution],
    is_considered: Callable[[ConsiderationItemRef], bool],
    open_items: Set[OnScreenWorkspaceItem] = frozenset(),
) -> tuple[WorkspaceBrowserSection, ...]:
    """Return the sidebar sections for the items' *resolutions*; an item that failed to resolve gets one row.

    *is_considered* answers whether a consideration item participates in navigation and layout; *open_items* are the
    items currently shown in a viewer.
    """
    return _RowProjection(is_considered).sections(resolutions, open_items)
