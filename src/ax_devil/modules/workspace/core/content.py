"""Workspace-owned content descriptions and playlist structure."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast

from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.workspace.core.consideration import ConsiderationItem, ConsiderationItemRef, OnScreenWorkspaceItem
from ax_devil.modules.workspace.core.source_specs import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    InfoFields,
    LiveOverlaySourceSpec,
    LiveRTSPStreamSpec,
    OverlaySourceKind,
    OverlaySourceSpec,
)


@dataclass(frozen=True, slots=True)
class OverlayContent:
    """An overlay lane with a label, source spec, and plugin-defined metadata."""

    display_name: str
    source_spec: OverlaySourceSpec
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def source_kind(self) -> OverlaySourceKind:
        """Return the kind owned by this overlay's source specification."""
        return self.source_spec.source_kind


@dataclass(frozen=True, slots=True)
class LiveVideoContent:
    """A live workspace video with zero or more live overlay lanes.

    - 0 overlays: plain video playback
    - 1 overlay: video with overlay rendered on top
    Live playback supports at most one RTSP, MQTT, or DataHub WebSocket overlay source.
    """

    display_name: str
    source_spec: LiveRTSPStreamSpec
    overlays: tuple[OverlayContent, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    content_id: str = ""
    item_id: str = ""
    is_live: ClassVar[bool] = True
    source_paths: ClassVar[SourcePaths] = SourcePaths()

    def __post_init__(self) -> None:
        """Reject overlay shapes unsupported by live playback."""
        if len(self.overlays) > 1:
            raise ValueError("Live video content supports at most one overlay.")
        if any(not isinstance(overlay.source_spec, LiveOverlaySourceSpec) for overlay in self.overlays):
            raise TypeError(
                "Live video content requires an RTSP or MQTT overlay source spec; WebSocket overlays are supported too."
            )

    @property
    def source_location(self) -> str:
        """Return the device, and the camera head when it is not the first; the browser shows it beside the name."""
        head = self.source_spec.camera_head
        return self.source_spec.host if head == 1 else f"{self.source_spec.host} · head {head}"

    @property
    def overlay_spec(self) -> LiveOverlaySourceSpec | None:
        """Return the source spec of the live overlay, or None for plain video."""
        return cast(LiveOverlaySourceSpec, self.overlays[0].source_spec) if self.overlays else None

    def info_fields(self) -> InfoFields:
        """Return the content type, overlay count, and stream settings."""
        return (("type", "live video"), ("overlays", len(self.overlays)), *self.source_spec.info_fields())

    def standalone_lanes(self) -> tuple[EntryLane, ...]:
        """Return the visible lanes for this standalone live video item."""
        if self.overlays:
            return tuple(create_entry_lane(self, default_considered=True, overlay=overlay) for overlay in self.overlays)
        return (create_entry_lane(self, default_considered=True),)

    def consideration_items(self) -> tuple[ConsiderationItem, ...]:
        """Return no items because live overlay consideration is unsupported."""
        return ()

    def lane_consideration_ref(self, entry_index: int, lane_index: int) -> ConsiderationItemRef:
        """Return the reference of one lane, which no consideration item lists."""
        return ConsiderationItemRef(self.item_id, "", lane_index)

    @property
    def bookmark_entries(self) -> tuple[str, ...]:
        """Return no Bookmark Target entries: live video is never bookmarked."""
        return ()

    @property
    def bookmark_entry_titles(self) -> tuple[str, ...]:
        """Return no entry titles, one per Bookmark Target entry."""
        return ()

    def on_screen_item(self, entry_index: int = 0) -> OnScreenWorkspaceItem:
        """Return the workspace item a viewer shows for this video."""
        return OnScreenWorkspaceItem(kind="video", content_id=self.content_id)


@dataclass(frozen=True, slots=True)
class SeekableVideoContent:
    """A seekable workspace video with zero or more overlays."""

    display_name: str
    source_spec: FileVideoSourceSpec
    overlays: tuple[OverlayContent, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    content_id: str = ""
    item_id: str = ""
    is_live: ClassVar[bool] = False

    def __post_init__(self) -> None:
        """Reject overlay shapes unsupported by seekable playback."""
        if any(not isinstance(overlay.source_spec, FileOverlaySourceSpec) for overlay in self.overlays):
            raise TypeError("Seekable video content requires file overlay source specs.")

    @property
    def source_location(self) -> str:
        """Return the video file path, shown to tell same-named items apart."""
        return str(self.source_spec.path)

    @property
    def source_paths(self) -> SourcePaths:
        """Return the overlay files; the video file is not included, as changes to it do not matter."""
        return SourcePaths.merged(overlay.source_spec.source_paths for overlay in self.overlays)

    def info_fields(self) -> InfoFields:
        """Return the content type, overlay count, and video file settings."""
        return (("type", "seekable video"), ("overlays", len(self.overlays)), *self.source_spec.info_fields())

    def standalone_lanes(self) -> tuple[EntryLane, ...]:
        """Return the visible lanes for this standalone seekable video item."""
        if not self.overlays:
            return (create_entry_lane(self, default_considered=True),)
        if len(self.overlays) == 1:
            return (create_entry_lane(self, default_considered=True, overlay=self.overlays[0]),)
        return tuple(create_entry_lane(self, default_considered=True, overlay=overlay) for overlay in self.overlays)

    def consideration_items(self) -> tuple[ConsiderationItem, ...]:
        """Return the standalone overlay lanes whose layout can be toggled."""
        return tuple(
            ConsiderationItem(
                ref=self.lane_consideration_ref(0, lane_index),
                default_considered=lane.default_considered,
            )
            for lane_index, lane in enumerate(self.standalone_lanes())
            if lane.source_kind != OverlaySourceKind.NO_SOURCE
        )

    @property
    def entries(self) -> tuple[PlaylistEntry, ...]:
        """Return this video as a one-entry playlist of its standalone lanes."""
        return (PlaylistEntry(lanes=self.standalone_lanes(), default_considered=True),)

    @property
    def bookmark_entries(self) -> tuple[str, ...]:
        """Return the Bookmark Target entry of the one entry: empty, since the target is the item itself."""
        return ("",)

    @property
    def bookmark_entry_titles(self) -> tuple[str, ...]:
        """Return the title of the one entry: empty, since the item's own name is its title."""
        return ("",)

    def entry_consideration_ref(self, entry_index: int) -> None:
        """Return None because the single entry of a standalone video is always considered."""
        return None

    def lane_consideration_ref(self, entry_index: int, lane_index: int) -> ConsiderationItemRef:
        """Return the reference that toggles one standalone overlay lane."""
        return ConsiderationItemRef(self.item_id, "", lane_index)

    def on_screen_item(self, entry_index: int = 0) -> OnScreenWorkspaceItem:
        """Return the workspace item a viewer shows for this video."""
        return OnScreenWorkspaceItem(kind="video", content_id=self.content_id)


@dataclass(frozen=True, slots=True)
class EntryLane:
    """One visible comparison lane within a playlist entry."""

    display_name: str
    video: SeekableVideoContent | LiveVideoContent
    default_considered: bool
    overlay: OverlayContent | None = None
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        """Reject overlay sources incompatible with the lane's video workflow."""
        if self.overlay is None:
            return
        if isinstance(self.video, SeekableVideoContent) and not isinstance(
            self.overlay.source_spec, FileOverlaySourceSpec
        ):
            raise TypeError("Seekable video lanes require file overlay source specs.")
        if isinstance(self.video, LiveVideoContent) and not isinstance(self.overlay.source_spec, LiveOverlaySourceSpec):
            raise TypeError(
                "Live video lanes require an RTSP or MQTT overlay source spec; WebSocket overlays are supported too."
            )

    @property
    def source_kind(self) -> OverlaySourceKind:
        """Return the overlay source kind, or no-source for a plain video lane."""
        return self.overlay.source_kind if self.overlay is not None else OverlaySourceKind.NO_SOURCE

    @property
    def source_location(self) -> str:
        """Return the overlay file location, or the video location for plain and live lanes."""
        if self.overlay is not None and self.overlay.source_spec.source_location is not None:
            return self.overlay.source_spec.source_location
        return self.video.source_location


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    """One step in a playlist — everything shown simultaneously.

    Each lane is one visible panel. Multiple lanes may share the same video
    but differ by overlay, or may refer to distinct videos.
    """

    lanes: tuple[EntryLane, ...]
    default_considered: bool
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        """Reject unsupported empty or live playlist lanes before they enter the Workspace."""
        if not self.lanes:
            raise ValueError("Playlist entries require at least one lane.")
        if any(not isinstance(lane.video, SeekableVideoContent) for lane in self.lanes):
            raise TypeError("Playlist entries require seekable video content.")

    def display_label(self, entry_index: int) -> str:
        """Return the default label for this entry in workspace views."""
        unique_videos = {lane.video for lane in self.lanes}
        if len(unique_videos) == 1:
            return self.lanes[0].video.display_name
        if len(self.lanes) == 1:
            return self.lanes[0].display_name
        return f"Entry {entry_index + 1}"

    @property
    def source_location(self) -> str | None:
        """Return the entry's video location when all lanes show one video, otherwise None."""
        unique_videos = {lane.video for lane in self.lanes}
        if len(unique_videos) == 1:
            return self.lanes[0].video.source_location
        return None

    @property
    def source_paths(self) -> SourcePaths:
        """Return the overlay files of all lanes."""
        return SourcePaths.merged(
            lane.overlay.source_spec.source_paths for lane in self.lanes if lane.overlay is not None
        )

    def should_show_lane_children(self) -> bool:
        """Return whether UI consumers should render child lanes for this entry."""
        return len(self.lanes) > 1 or any(lane.source_kind != OverlaySourceKind.NO_SOURCE for lane in self.lanes)


@dataclass(frozen=True, slots=True)
class PlaylistContent:
    """An ordered sequence of entries to step through."""

    display_name: str
    entries: tuple[PlaylistEntry, ...]
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    content_id: str = ""
    item_id: str = ""
    source_name: str = ""
    """The name the resolver gave the playlist, numbered when its item resolved to several of that name.

    ``display_name`` is renamed after the item's label, this never is.
    """
    bookmark_entries: tuple[str, ...] = field(init=False, compare=False, repr=False)
    """Each entry's Bookmark Target entry, "<playlist name>/<entry title>", in entry order.

    The playlist is named as its resolver named it, so renaming the item changes no key, and one Playlist Item may
    resolve to several playlists, whose entries the playlist name tells apart.
    """

    def __post_init__(self) -> None:
        """Reject empty playlists before they enter the Workspace, and remember the resolver's name for the playlist."""
        if not self.entries:
            raise ValueError("Playlists require at least one entry.")
        if not self.source_name:
            object.__setattr__(self, "source_name", self.display_name)
        titles = self.bookmark_entry_titles
        object.__setattr__(self, "bookmark_entries", tuple(f"{self.source_name}/{title}" for title in titles))

    @property
    def source_location(self) -> None:
        """Return None because playlists are resolved from several sources."""
        return None

    @property
    def source_paths(self) -> SourcePaths:
        """Return the overlay files of all entries."""
        return SourcePaths.merged(entry.source_paths for entry in self.entries)

    def info_fields(self) -> InfoFields:
        """Return the content type and entry and lane counts."""
        lane_count = sum(len(entry.lanes) for entry in self.entries)
        return (("type", "playlist"), ("entries", len(self.entries)), ("lanes", lane_count))

    def consideration_items(self) -> tuple[ConsiderationItem, ...]:
        """Return every playlist entry and lane whose participation can be toggled."""
        items: list[ConsiderationItem] = []
        for entry_index, entry in enumerate(self.entries):
            items.append(
                ConsiderationItem(
                    ref=self.entry_consideration_ref(entry_index),
                    default_considered=entry.default_considered,
                )
            )
            if entry.should_show_lane_children():
                items.extend(
                    ConsiderationItem(
                        ref=self.lane_consideration_ref(entry_index, lane_index),
                        default_considered=lane.default_considered,
                    )
                    for lane_index, lane in enumerate(entry.lanes)
                )
        return tuple(items)

    @property
    def bookmark_entry_titles(self) -> tuple[str, ...]:
        """Return each entry's title in entry order: its label, numbered " (2)", " (3)" when entries share a label.

        Entries from one resolver run in a fixed order, so a title names the same entry on every run.
        """
        return numbered_when_shared(entry.display_label(index) for index, entry in enumerate(self.entries))

    def entry_consideration_ref(self, entry_index: int) -> ConsiderationItemRef:
        """Return the reference that toggles one playlist entry."""
        return ConsiderationItemRef(self.item_id, self.bookmark_entries[entry_index])

    def lane_consideration_ref(self, entry_index: int, lane_index: int) -> ConsiderationItemRef:
        """Return the reference that toggles one lane of a playlist entry."""
        return ConsiderationItemRef(self.item_id, self.bookmark_entries[entry_index], lane_index)

    def on_screen_item(self, entry_index: int = 0) -> OnScreenWorkspaceItem:
        """Return the workspace item a viewer shows for one playlist entry."""
        return OnScreenWorkspaceItem(
            kind="playlist_entry", content_id=self.content_id, entry=self.bookmark_entries[entry_index]
        )


Content = SeekableVideoContent | LiveVideoContent | PlaylistContent
"""What a Workspace Item resolves to.

Resolution sets ``item_id`` to the owning item's id and ``content_id`` to an id derived from it, so both stay the same
every time the item resolves. Content built outside resolution, such as a playlist lane's video, leaves them empty.
"""


def numbered_when_shared(labels: Iterable[str]) -> tuple[str, ...]:
    """Return *labels* with the second and later of equal ones numbered " (2)", " (3)", so no two are equal.

    A number is skipped when it would equal another label, such as an existing "video (2)".
    """
    labels = tuple(labels)
    taken = set(labels)
    seen: set[str] = set()
    numbered = []
    for label in labels:
        name = label
        if label in seen:
            number = 2
            while (name := f"{label} ({number})") in taken:
                number += 1
            taken.add(name)
        seen.add(label)
        numbered.append(name)
    return tuple(numbered)


def create_entry_lane(
    video: SeekableVideoContent | LiveVideoContent,
    *,
    default_considered: bool,
    display_name: str | None = None,
    overlay: OverlayContent | None = None,
    metadata: dict[str, Any] | None = None,
) -> EntryLane:
    """Build one visible lane with consistent label, kind, and metadata defaults."""
    return EntryLane(
        display_name=display_name or (overlay.display_name if overlay is not None else video.display_name),
        video=video,
        default_considered=default_considered,
        overlay=overlay,
        metadata=metadata if metadata is not None else {},
    )
