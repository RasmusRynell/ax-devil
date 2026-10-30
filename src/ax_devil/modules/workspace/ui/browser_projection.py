"""Projection of Workspace items, their Content, consideration state, and open items into sidebar browser rows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import replace
from functools import partial
from pathlib import PurePath
from types import MappingProxyType

from ax_devil.modules.thumbnails import SNAPSHOT_TIMEOUT_S, CameraThumbnail, FileThumbnail, ThumbnailSource
from ax_devil.modules.workspace.core.consideration import ConsiderationItemRef, OnScreenWorkspaceItem
from ax_devil.modules.workspace.core.content import (
    Content,
    EntryLane,
    LiveVideoContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.core.item_base import WorkspaceItem
from ax_devil.modules.workspace.core.item_info import (
    build_playlist_entry_information,
    build_playlist_information,
    build_playlist_lane_information,
    build_unavailable_information,
    build_video_information,
    build_video_lane_information,
)
from ax_devil.modules.workspace.core.items import ItemResolution, PlaylistItem, VideoItem
from ax_devil.modules.workspace.core.live_stream_item import LiveStreamItem
from ax_devil.modules.workspace.core.source_specs import OverlaySourceKind
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserOpenTarget,
    BrowserSection,
    OfferedAction,
    RowAction,
    WorkspaceBrowserIconKind,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.live_stream_status import LiveStreamStatus
from ax_devil.modules.workspace.ui.row_details import with_details

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


def _with_actions(row: WorkspaceBrowserRow, inline: RowAction | None = None) -> WorkspaceBrowserRow:
    """Return *row* offering what its fields allow, and *inline* as its button.

    A row that cannot open still offers Open, which says why. Export needs the row open in a viewer, Hide or Unhide
    is for a row playback can skip.
    """
    offered: list[OfferedAction] = []
    hidden_lane = (
        row.activation_target is not None and row.activation_target.lane_index is not None and not row.is_considered
    )
    open_reason = "Hidden lane: choose Unhide and open" if hidden_lane else ""
    if row.activation_target is not None or row.unavailable_reason is not None:
        offered.append(OfferedAction(RowAction.OPEN, open_reason))
    if row.activation_target is not None:
        offered.append(OfferedAction(RowAction.OPEN_TO_SIDE, open_reason))
        if hidden_lane:
            offered.append(OfferedAction(RowAction.UNHIDE_AND_OPEN))
    if inline is not None:
        offered.append(OfferedAction(inline))
    if row.information_factory is not None:
        offered.append(OfferedAction(RowAction.INFORMATION))
    if row.export_target is not None:
        offered.append(OfferedAction(RowAction.EXPORT_VIDEO, "" if row.is_open else "Open it first"))
    if row.consideration_ref is not None:
        offered.append(OfferedAction(RowAction.HIDE if row.is_considered else RowAction.UNHIDE))
    if row.item is not None:
        if row.item.editable:
            offered.append(OfferedAction(RowAction.EDIT))
        offered.append(OfferedAction(RowAction.REMOVE))
    return replace(row, actions=tuple(offered))


def _thumbnail(video: SeekableVideoContent | LiveVideoContent) -> ThumbnailSource:
    """Return what a hover card shows a frame of for *video*: its camera, or its file's first frame."""
    if isinstance(video, LiveVideoContent):  # is_live does not narrow the type for mypy.
        spec = video.source_spec
        return CameraThumbnail(video.content_id, spec.connection(SNAPSHOT_TIMEOUT_S), spec.camera_head)
    return FileThumbnail(video.source_spec.path, video.source_spec.image_sequence_config)


def _counted(count: int, noun: str, plural: str = "") -> str:
    """Return *count* followed by *noun*, or by *plural* (default *noun* plus s) unless the count is one."""
    return f"{count} {noun}" if count == 1 else f"{count} {plural or f'{noun}s'}"


def _lane_fact(lane: EntryLane) -> str:
    """Return what a lane row shows: an overlay, or the plain video."""
    return "Video" if lane.overlay is None else "Overlay"


class _RowProjection:
    """Build browser rows, asking *is_considered* for the consideration state of each referenced item; a camera row
    shows the most urgent of the *stream_statuses* of the viewers of its content, by content id."""

    def __init__(
        self,
        is_considered: Callable[[ConsiderationItemRef], bool],
        stream_statuses: Mapping[str, Sequence[LiveStreamStatus]],
    ) -> None:
        self._is_considered = is_considered
        self._stream_statuses = stream_statuses
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
            WorkspaceBrowserSection(kind=section, rows=with_details(tuple(section_rows), self._file_paths))
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
                    activation_target=BrowserOpenTarget(content, None),
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
        stream = (
            LiveStreamStatus.of_viewers(self._stream_statuses.get(content.content_id, ())) if content.is_live else None
        )
        host_part = (
            content.source_location if content.is_live and content.source_location != content.display_name else ""
        )
        return _with_actions(
            WorkspaceBrowserRow(
                row_id=content.content_id,
                label=content.display_name,
                icon_kind=self._icon_kind_for_video(content),
                activation_target=None if content.is_live else BrowserOpenTarget(content, 0),
                item=item,
                information_factory=partial(build_video_information, content),
                export_target=None if content.is_live else content.on_screen_item(),
                is_open=is_open,
                children=self._video_lane_rows(content),
                location=content.source_location,
                detail=" · ".join(part for part in (host_part, stream.text if stream else "") if part),
                facts=(f"{'Live stream' if content.is_live else 'Video'}{overlays}",),
                idle=content.is_live and not is_open,
                stream=stream,
                thumbnail=_thumbnail(content),
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
        row_id = f"{playlist.content_id}/entry/{playlist.bookmark_entries[entry_index]}"
        ref = playlist.entry_consideration_ref(entry_index)
        if entry.source_location is not None:
            self._file_paths[row_id] = PurePath(entry.source_location)
        return _with_actions(
            WorkspaceBrowserRow(
                row_id=row_id,
                label=entry.display_label(entry_index),
                icon_kind=self._icon_kind_for_playlist_entry(entry),
                activation_target=BrowserOpenTarget(playlist, entry_index),
                consideration_ref=ref,
                information_factory=partial(build_playlist_entry_information, playlist, entry, entry_index),
                export_target=open_item,
                is_considered=self._is_considered(ref),
                is_open=open_item in open_items,
                children=children,
                location=entry.source_location,
                facts=(f"Entry {entry_index + 1} of {len(playlist.entries)}",),
                thumbnail=_thumbnail(entry.lanes[0].video),
            )
        )

    def _video_lane_rows(self, video: SeekableVideoContent | LiveVideoContent) -> tuple[WorkspaceBrowserRow, ...]:
        """Build child rows for visible standalone video lanes."""
        consideration_items = {item.ref: item for item in video.consideration_items()}
        rows: list[WorkspaceBrowserRow] = []
        for lane_index, lane in enumerate(video.standalone_lanes()):
            if lane.source_kind == OverlaySourceKind.NO_SOURCE:
                continue
            lane_ref = video.lane_consideration_ref(0, lane_index)
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
                        activation_target=None if video.is_live else BrowserOpenTarget(video, 0, lane_index),
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
        row_id = f"{playlist.content_id}/entry/{playlist.bookmark_entries[entry_index]}/lane/{lane_index}"
        ref = playlist.lane_consideration_ref(entry_index, lane_index)
        self._file_paths[row_id] = PurePath(lane.source_location)
        return _with_actions(
            WorkspaceBrowserRow(
                row_id=row_id,
                label=lane.display_name,
                icon_kind="overlay",
                activation_target=BrowserOpenTarget(playlist, entry_index, lane_index),
                consideration_ref=ref,
                information_factory=partial(
                    build_playlist_lane_information,
                    playlist,
                    entry,
                    entry_index,
                    lane,
                    lane_index,
                ),
                is_considered=self._is_considered(ref),
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
    *,
    stream_statuses: Mapping[str, Sequence[LiveStreamStatus]] = MappingProxyType({}),
) -> tuple[WorkspaceBrowserSection, ...]:
    """Return the sidebar sections for the items' *resolutions*; an item that failed to resolve gets one row.

    *is_considered* answers whether a consideration item participates in navigation and layout; *open_items* are the
    items currently shown in a viewer, and *stream_statuses* the state of each viewer's stream by content id.
    """
    return _RowProjection(is_considered, stream_statuses).sections(resolutions, open_items)
