"""Tests for the content-browser row projection."""

from __future__ import annotations

from collections.abc import Set
from pathlib import Path
from unittest.mock import patch

import pytest

from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    Content,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveRTSPStreamSpec,
    LiveStreamItem,
    LiveVideoContent,
    OnScreenWorkspaceItem,
    OverlayContent,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    SeekableVideoContent,
    VideoItem,
    WorkspaceItem,
)
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.core.items import UnreadableItem
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow, build_browser_rows
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_live_video, make_playlist, make_video
from tests.helpers.workspace import FakeResolutionContext, content_item, inline_resolver, store_with


def browser_rows(
    store: WorkspaceStore, open_items: Set[OnScreenWorkspaceItem] = frozenset()
) -> tuple[WorkspaceBrowserRow, ...]:
    """Project the store's items the way the workspace controller does."""
    return build_browser_rows(store.resolutions(), store.is_item_considered, open_items)


def item_rows(
    store: WorkspaceStore, open_items: Set[OnScreenWorkspaceItem] = frozenset()
) -> tuple[WorkspaceBrowserRow, ...]:
    """Return the item rows under the sections, in the order they were added."""
    return tuple(row for section in browser_rows(store, open_items) for row in section.children)


def _store(*contents: Content) -> WorkspaceStore:
    """Return a store with one item per content."""
    store = WorkspaceStore(inline_resolver(FakeResolutionContext()))
    store.add_items([content_item(content) for content in contents])
    return store


def test_open_items_are_inputs_to_browser_projection() -> None:
    playlist = make_playlist()
    state, playlist = store_with(playlist)
    first_open_item = frozenset(
        {
            OnScreenWorkspaceItem(kind="playlist_entry", content_id=playlist.content_id, entry_index=0),
        }
    )
    second_open_item = frozenset(
        {
            OnScreenWorkspaceItem(kind="playlist_entry", content_id=playlist.content_id, entry_index=1),
        }
    )

    first_rows = item_rows(state, first_open_item)
    second_rows = item_rows(state, second_open_item)

    assert first_rows[0].children[0].is_open
    assert not first_rows[0].children[1].is_open
    assert not second_rows[0].children[0].is_open
    assert second_rows[0].children[1].is_open


def test_video_rows_include_overlay_children_targets_refs_and_information() -> None:
    video = make_video("test.mp4", overlay_count=2)
    state, video = store_with(video)
    state.set_item_considered(ConsiderationItemRef.video_lane(video.content_id, 1), False)
    open_items = frozenset({OnScreenWorkspaceItem(kind="video", content_id=video.content_id)})

    row = item_rows(state, open_items)[0]

    assert row.label == "test.mp4"
    assert row.icon_kind == "video"
    assert row.activation_target == (video, 0)
    assert row.item == state.workspace.items[0]
    assert row.is_open
    assert row.information_factory is not None
    information = row.information_factory()
    assert ("path", "/tmp/test.mp4") in information.fields
    assert information.children[0].title == "Overlay 1"
    assert ("path", "/tmp/Overlay 1.txt") in information.children[0].fields
    assert ("handler type", "AXIS_JSON") in information.children[0].fields
    assert [child.label for child in row.children] == ["Overlay 1", "Overlay 2"]
    assert row.children[0].activation_target == (video, 0)
    assert row.children[0].consideration_ref == ConsiderationItemRef.video_lane(video.content_id, 0)
    assert row.children[0].is_considered
    assert not row.children[1].is_considered


def test_browser_rows_defer_information_building_until_requested() -> None:
    video = make_video("test.mp4", overlay_count=2)
    state, video = store_with(video)
    information = WorkspaceItemInfo(title="test.mp4", fields=(("type", "seekable video"),))

    with patch(
        "ax_devil.modules.workspace.ui.browser_rows.build_video_information",
        return_value=information,
    ) as build_information:
        row = item_rows(state)[0]

        build_information.assert_not_called()
        information_factory = row.information_factory
        assert information_factory is not None
        assert information_factory() == information

    build_information.assert_called_once_with(video)


def test_live_rows_use_live_icon_and_embedded_overlay_child() -> None:
    live_video = make_live_video(overlay_source=OverlaySourceKind.RTSP_SOURCE)
    state, live_video = store_with(live_video)

    row = item_rows(state)[0]

    assert row.icon_kind == "live_video"
    assert [child.label for child in row.children] == ["RTSP"]
    assert row.children[0].consideration_ref is None


def test_playlist_rows_include_entries_lanes_open_flags_and_information_payloads() -> None:
    playlist = PlaylistContent(
        display_name="Suite",
        metadata={"resolver": "Synthetic"},
        entries=(
            PlaylistEntry(
                lanes=make_video("a.mp4", overlay_count=2).standalone_lanes(),
                default_considered=True,
                metadata={"note": "first entry"},
            ),
            PlaylistEntry(lanes=make_video("b.mp4").standalone_lanes(), default_considered=True),
        ),
    )
    state, playlist = store_with(playlist)
    open_items = frozenset(
        {OnScreenWorkspaceItem(kind="playlist_entry", content_id=playlist.content_id, entry_index=1)}
    )

    row = item_rows(state, open_items)[0]
    first_entry = row.children[0]
    second_entry = row.children[1]

    assert row.label == "Suite"
    assert row.icon_kind == "playlist"
    assert row.activation_target == (playlist, 0)
    assert row.item == state.workspace.items[0]
    assert row.is_open
    assert row.information_factory is not None
    assert ("resolver", "Synthetic") in row.information_factory().fields
    assert first_entry.label == "a.mp4"
    assert first_entry.activation_target == (playlist, 0)
    assert first_entry.consideration_ref == ConsiderationItemRef.playlist_entry(playlist.content_id, 0)
    assert not first_entry.is_open
    assert first_entry.children[0].consideration_ref == ConsiderationItemRef.playlist_lane(playlist.content_id, 0, 0)
    assert first_entry.information_factory is not None
    assert ("note", "first entry") in first_entry.information_factory().fields
    assert second_entry.is_open


def test_multi_video_playlist_entry_uses_playlist_icon() -> None:
    playlist = PlaylistContent(
        display_name="Suite",
        entries=(
            PlaylistEntry(
                lanes=(
                    make_video("a.mp4").standalone_lanes()[0],
                    make_video("b.mp4").standalone_lanes()[0],
                ),
                default_considered=True,
            ),
        ),
    )
    state, playlist = store_with(playlist)

    row = item_rows(state)[0]

    assert row.children[0].icon_kind == "playlist"


def test_same_named_rows_get_shortest_distinguishing_folder_hint() -> None:
    def video(path: str) -> SeekableVideoContent:
        return SeekableVideoContent(display_name=Path(path).name, source_spec=FileVideoSourceSpec(path=Path(path)))

    store = _store(
        video("/data/site_a/parking_lot_cam3.mp4"),
        video("/data/site_b/parking_lot_cam3.mp4"),
        video("/data/site_c/day1/gate.mp4"),
        video("/data/site_d/day1/gate.mp4"),
        video("/data/site_a/unique.mp4"),
    )

    rows = item_rows(store)

    assert [(row.label, row.detail) for row in rows] == [
        ("parking_lot_cam3.mp4", "site_a"),
        ("parking_lot_cam3.mp4", "site_b"),
        ("gate.mp4", "site_c/day1"),
        ("gate.mp4", "site_d/day1"),
        ("unique.mp4", ""),
    ]
    assert rows[0].tooltip == "Video\n/data/site_a/parking_lot_cam3.mp4"


def test_same_named_playlist_entries_get_folder_hints() -> None:
    first = SeekableVideoContent(display_name="clip.mp4", source_spec=FileVideoSourceSpec(path=Path("/a/clip.mp4")))
    second = SeekableVideoContent(display_name="clip.mp4", source_spec=FileVideoSourceSpec(path=Path("/b/clip.mp4")))
    playlist = PlaylistContent(
        display_name="Playlist",
        entries=(
            PlaylistEntry(lanes=first.standalone_lanes(), default_considered=True),
            PlaylistEntry(lanes=second.standalone_lanes(), default_considered=True),
        ),
    )
    store = _store(playlist)

    (row,) = item_rows(store)

    assert [(child.label, child.detail) for child in row.children] == [("clip.mp4", "a"), ("clip.mp4", "b")]
    assert (row.label, row.detail) == ("Playlist", "2 entries")


@pytest.mark.parametrize("in_playlist", [False, True])
def test_same_named_overlay_lanes_show_overlay_folder_hints_and_paths(in_playlist: bool) -> None:
    video = SeekableVideoContent(
        display_name="clip.mp4",
        source_spec=FileVideoSourceSpec(path=Path("/videos/clip.mp4")),
        overlays=tuple(
            OverlayContent(
                display_name="tracks.txt",
                source_spec=FileOverlaySourceSpec(path=Path(path), handler_type="AXIS_JSON"),
            )
            for path in ("/detector_a/tracks.txt", "/detector_b/tracks.txt")
        ),
    )
    if in_playlist:
        playlist = PlaylistContent(
            display_name="Comparison",
            entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
        )
        lane_rows = item_rows(_store(playlist))[0].children[0].children
    else:
        lane_rows = item_rows(_store(video))[0].children

    assert [(row.label, row.detail) for row in lane_rows] == [
        ("tracks.txt", "detector_a"),
        ("tracks.txt", "detector_b"),
    ]
    assert [row.tooltip for row in lane_rows] == [
        "Overlay\n/detector_a/tracks.txt",
        "Overlay\n/detector_b/tracks.txt",
    ]


def test_same_named_rows_in_one_folder_fall_back_to_file_names() -> None:
    store = _store(
        SeekableVideoContent(display_name="Camera", source_spec=FileVideoSourceSpec(path=Path("/clips/front.mp4"))),
        SeekableVideoContent(display_name="Camera", source_spec=FileVideoSourceSpec(path=Path("/clips/rear.mp4"))),
    )

    assert [(row.label, row.detail) for row in item_rows(store)] == [
        ("Camera", "front.mp4"),
        ("Camera", "rear.mp4"),
    ]


def test_same_named_live_streams_are_told_apart_by_host_and_camera_head() -> None:
    def live(host: str, head: int) -> LiveVideoContent:
        return LiveVideoContent(
            display_name="Live",
            source_spec=LiveRTSPStreamSpec(host=host, username="root", password="secret", camera_head=head),
        )

    store = _store(live("camera", 1), live("camera", 2), live("other", 1))

    rows = item_rows(store)

    assert [(row.label, row.detail) for row in rows] == [
        ("Live", "camera"),
        ("Live", "camera · head 2"),
        ("Live", "other"),
    ]
    assert rows[1].tooltip == "Live stream\ncamera · head 2"
    assert all("secret" not in row.tooltip for row in rows)


def test_repeated_sources_are_numbered() -> None:
    def video(path: str) -> SeekableVideoContent:
        return SeekableVideoContent(display_name="clip.mp4", source_spec=FileVideoSourceSpec(path=Path(path)))

    store = _store(video("/a/clip.mp4"), video("/a/clip.mp4"), video("/b/clip.mp4"))

    assert [(row.label, row.detail) for row in item_rows(store)] == [
        ("clip.mp4", "a (1)"),
        ("clip.mp4", "a (2)"),
        ("clip.mp4", "b"),
    ]


def test_same_named_rows_without_locations_are_numbered() -> None:
    def playlist() -> PlaylistContent:
        video = SeekableVideoContent(display_name="clip.mp4", source_spec=FileVideoSourceSpec(path=Path("/a/clip.mp4")))
        entry = PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True)
        return PlaylistContent(display_name="Folder Pair", entries=(entry,))

    store = _store(playlist(), playlist())

    rows = item_rows(store)

    assert [(row.label, row.detail) for row in rows] == [("Folder Pair", "(1)"), ("Folder Pair", "(2)")]
    assert [(row.children[0].label, row.children[0].detail) for row in rows] == [("clip.mp4", ""), ("clip.mp4", "")]


class _FixedPlaylists:
    """A playlist resolver that returns the playlists it was built with."""

    def __init__(self, *playlists: PlaylistContent) -> None:
        self._playlists = list(playlists)

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        return self._playlists


def _store_of_items(*items: WorkspaceItem) -> WorkspaceStore:
    """Return a store resolving real *items*, with a playlist resolver named "fixed" that yields one playlist."""
    context = FakeResolutionContext(resolvers={"fixed": _FixedPlaylists(make_playlist("Suite"))})
    store = WorkspaceStore(inline_resolver(context))
    store.add_items(items)
    return store


def test_sections_group_rows_by_kind_in_a_fixed_order_with_counts(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "b.mp4").write_bytes(b"")
    store = _store_of_items(
        VideoItem(video=tmp_path / "a.mp4"),
        PlaylistItem(resolver="fixed"),
        LiveStreamItem(label="cam", host="camera.local"),
        VideoItem(video=tmp_path / "b.mp4"),
        VideoItem(video=tmp_path / "missing.mp4"),
        UnreadableItem.from_raw({"kind": "future"}, "Unknown kind"),
    )

    sections = browser_rows(store)

    assert [(s.row_id, s.label, s.summary, s.is_section) for s in sections] == [
        ("section/live", "Live", "1", True),
        ("section/videos", "Videos", "3", True),
        ("section/playlists", "Playlists", "1", True),
        ("section/other", "Other", "1", True),
    ]
    assert [[row.label for row in section.children] for section in sections] == [
        ["cam"],
        ["a.mp4", "b.mp4", "missing.mp4"],
        ["Suite"],
        ["future"],
    ]
    missing = sections[1].children[2]
    assert (missing.detail, missing.unavailable_reason) == (
        "Unavailable",
        f"File not found: {tmp_path / 'missing.mp4'}",
    )
    assert sections[3].children[0].detail == "Unavailable"


def test_sections_are_present_only_for_kinds_in_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"")

    sections = browser_rows(_store_of_items(PlaylistItem(resolver="fixed"), VideoItem(video=tmp_path / "a.mp4")))

    assert [section.label for section in sections] == ["Videos", "Playlists"]
    assert browser_rows(_store_of_items()) == ()


def test_same_names_in_different_sections_get_no_location_hint(tmp_path: Path) -> None:
    (tmp_path / "site_a").mkdir()
    (tmp_path / "site_a" / "front.mp4").write_bytes(b"")
    store = _store_of_items(
        VideoItem(video=tmp_path / "site_a" / "front.mp4"),
        LiveStreamItem(label="front.mp4", host="camera.local"),
    )

    live_section, videos_section = browser_rows(store)

    assert [(row.label, row.detail) for row in videos_section.children] == [("front.mp4", "")]
    assert [(row.label, row.detail) for row in live_section.children] == [("front.mp4", "camera.local")]


def test_same_names_in_one_section_get_location_hints_while_other_sections_do_not_count(tmp_path: Path) -> None:
    for folder in ("site_a", "site_b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "front.mp4").write_bytes(b"")
    store = _store_of_items(
        VideoItem(video=tmp_path / "site_a" / "front.mp4"),
        VideoItem(video=tmp_path / "site_b" / "front.mp4"),
        LiveStreamItem(label="front.mp4", host="camera.local"),
    )

    live_section, videos_section = browser_rows(store)

    assert [(row.label, row.detail) for row in videos_section.children] == [
        ("front.mp4", "site_a"),
        ("front.mp4", "site_b"),
    ]
    assert [(row.label, row.detail) for row in live_section.children] == [("front.mp4", "camera.local")]


def test_a_live_rows_detail_is_its_host_unless_the_name_is_the_host() -> None:
    def live(name: str) -> LiveVideoContent:
        return LiveVideoContent(
            display_name=name,
            source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="secret"),
        )

    named, unnamed = item_rows(_store(live("Gate"), live("camera.local")))

    assert (named.label, named.detail) == ("Gate", "camera.local")
    assert (unnamed.label, unnamed.detail) == ("camera.local", "")


def test_a_playlists_summary_counts_entries_and_those_left_out() -> None:
    playlist = PlaylistContent(
        display_name="Suite",
        entries=tuple(
            PlaylistEntry(lanes=make_video(f"{name}.mp4").standalone_lanes(), default_considered=True) for name in "abc"
        ),
    )
    store, playlist = store_with(playlist)

    assert item_rows(store)[0].detail == "3 entries"

    store.set_item_considered(ConsiderationItemRef.playlist_entry(playlist.content_id, 1), False)

    row = item_rows(store)[0]
    assert row.detail == "2 of 3 entries"
    assert row.tooltip.splitlines()[0] == "Playlist · 3 entries, 1 left out"


def test_search_text_includes_the_detail_and_a_section_has_none() -> None:
    live = LiveVideoContent(
        display_name="Gate", source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="secret")
    )
    (section,) = browser_rows(_store(live))

    assert section.search_text == ""
    assert section.children[0].search_text == "Gate camera.local"
