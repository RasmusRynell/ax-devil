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
    LiveVideoContent,
    OnScreenWorkspaceItem,
    OverlayContent,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow, build_browser_rows
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_live_video, make_playlist, make_video
from tests.helpers.workspace import FakeResolutionContext, content_item, store_with


def browser_rows(
    store: WorkspaceStore, open_items: Set[OnScreenWorkspaceItem] = frozenset()
) -> tuple[WorkspaceBrowserRow, ...]:
    """Project the store's contents the way the workspace controller does."""
    return build_browser_rows(store.contents(), store.is_item_considered, open_items)


def _store(*contents: Content) -> WorkspaceStore:
    """Return a store with one item per content."""
    store = WorkspaceStore(FakeResolutionContext())
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

    first_rows = browser_rows(state, first_open_item)
    second_rows = browser_rows(state, second_open_item)

    assert first_rows[0].children[0].is_open
    assert not first_rows[0].children[1].is_open
    assert not second_rows[0].children[0].is_open
    assert second_rows[0].children[1].is_open


def test_video_rows_include_overlay_children_targets_refs_and_information() -> None:
    video = make_video("test.mp4", overlay_count=2)
    state, video = store_with(video)
    state.set_item_considered(ConsiderationItemRef.video_lane(video.content_id, 1), False)
    open_items = frozenset({OnScreenWorkspaceItem(kind="video", content_id=video.content_id)})

    row = browser_rows(state, open_items)[0]

    assert row.label == "test.mp4"
    assert row.icon_kind == "video"
    assert row.activation_target == (video, 0)
    assert row.removable_content == video
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
        row = browser_rows(state)[0]

        build_information.assert_not_called()
        information_factory = row.information_factory
        assert information_factory is not None
        assert information_factory() == information

    build_information.assert_called_once_with(video)


def test_live_rows_use_live_icon_and_embedded_overlay_child() -> None:
    live_video = make_live_video(overlay_source=OverlaySourceKind.RTSP_SOURCE)
    state, live_video = store_with(live_video)

    row = browser_rows(state)[0]

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

    row = browser_rows(state, open_items)[0]
    first_entry = row.children[0]
    second_entry = row.children[1]

    assert row.label == "Suite"
    assert row.icon_kind == "playlist"
    assert row.activation_target == (playlist, 0)
    assert row.removable_content == playlist
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

    row = browser_rows(state)[0]

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

    rows = browser_rows(store)

    assert [row.display_text for row in rows] == [
        "parking_lot_cam3.mp4 — site_a",
        "parking_lot_cam3.mp4 — site_b",
        "gate.mp4 — site_c/day1",
        "gate.mp4 — site_d/day1",
        "unique.mp4",
    ]
    assert rows[0].tooltip == "/data/site_a/parking_lot_cam3.mp4"


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

    (row,) = browser_rows(store)

    assert [child.display_text for child in row.children] == ["clip.mp4 — a", "clip.mp4 — b"]
    assert row.display_text == "Playlist"


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
        lane_rows = browser_rows(_store(playlist))[0].children[0].children
    else:
        lane_rows = browser_rows(_store(video))[0].children

    assert [row.display_text for row in lane_rows] == ["tracks.txt — detector_a", "tracks.txt — detector_b"]
    assert [row.tooltip for row in lane_rows] == ["/detector_a/tracks.txt", "/detector_b/tracks.txt"]


def test_same_named_rows_in_one_folder_fall_back_to_file_names() -> None:
    store = _store(
        SeekableVideoContent(display_name="Camera", source_spec=FileVideoSourceSpec(path=Path("/clips/front.mp4"))),
        SeekableVideoContent(display_name="Camera", source_spec=FileVideoSourceSpec(path=Path("/clips/rear.mp4"))),
    )

    assert [row.display_text for row in browser_rows(store)] == ["Camera — front.mp4", "Camera — rear.mp4"]


def test_same_named_live_streams_are_told_apart_by_host_and_camera_head() -> None:
    def live(host: str, head: int) -> LiveVideoContent:
        return LiveVideoContent(
            display_name="Live",
            source_spec=LiveRTSPStreamSpec(host=host, username="root", password="secret", camera_head=head),
        )

    store = _store(live("camera", 1), live("camera", 2), live("other", 1))

    rows = browser_rows(store)

    assert [row.display_text for row in rows] == [
        "Live — camera/camera head 1",
        "Live — camera/camera head 2",
        "Live — other/camera head 1",
    ]
    assert rows[1].tooltip == "camera/camera head 2"
    assert all("secret" not in row.tooltip for row in rows)


def test_repeated_sources_are_numbered() -> None:
    def video(path: str) -> SeekableVideoContent:
        return SeekableVideoContent(display_name="clip.mp4", source_spec=FileVideoSourceSpec(path=Path(path)))

    store = _store(video("/a/clip.mp4"), video("/a/clip.mp4"), video("/b/clip.mp4"))

    assert [row.display_text for row in browser_rows(store)] == [
        "clip.mp4 — a (1)",
        "clip.mp4 — a (2)",
        "clip.mp4 — b",
    ]


def test_same_named_rows_without_locations_are_numbered() -> None:
    def playlist() -> PlaylistContent:
        video = SeekableVideoContent(display_name="clip.mp4", source_spec=FileVideoSourceSpec(path=Path("/a/clip.mp4")))
        entry = PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True)
        return PlaylistContent(display_name="Folder Pair", entries=(entry,))

    store = _store(playlist(), playlist())

    rows = browser_rows(store)

    assert [row.display_text for row in rows] == ["Folder Pair — (1)", "Folder Pair — (2)"]
    assert [row.children[0].display_text for row in rows] == ["clip.mp4", "clip.mp4"]
