"""Sibling rows that look alike are told apart by location hints or numbers."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveRTSPStreamSpec,
    LiveStreamItem,
    LiveVideoContent,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
    VideoItem,
)
from ax_devil.modules.workspace.core.items import ItemResolution, UnreadableItem
from ax_devil.modules.workspace.ui.browser_projection import build_browser_sections
from tests.helpers.browser_rows import browser_sections, item_rows, store_of, store_of_items


def test_same_named_rows_get_shortest_distinguishing_folder_hint() -> None:
    def video(path: str) -> SeekableVideoContent:
        return SeekableVideoContent(display_name=Path(path).name, source_spec=FileVideoSourceSpec(path=Path(path)))

    store = store_of(
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
        ("gate.mp4", str(Path("site_c/day1"))),
        ("gate.mp4", str(Path("site_d/day1"))),
        ("unique.mp4", ""),
    ]
    assert rows[0].tooltip == f"Video\n{Path('/data/site_a/parking_lot_cam3.mp4')}"


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
    store = store_of(playlist)

    (row,) = item_rows(store)

    assert [(child.label, child.detail) for child in row.children] == [("clip.mp4", "a"), ("clip.mp4", "b")]
    assert (row.label, row.detail) == ("Playlist", "")


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
        lane_rows = item_rows(store_of(playlist))[0].children[0].children
    else:
        lane_rows = item_rows(store_of(video))[0].children

    assert [(row.label, row.detail) for row in lane_rows] == [
        ("tracks.txt", "detector_a"),
        ("tracks.txt", "detector_b"),
    ]
    assert [row.tooltip for row in lane_rows] == [
        f"Overlay\n{Path('/detector_a/tracks.txt')}",
        f"Overlay\n{Path('/detector_b/tracks.txt')}",
    ]


def test_same_named_rows_in_one_folder_fall_back_to_file_names() -> None:
    store = store_of(
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

    store = store_of(live("camera", 1), live("camera", 2), live("other", 1))

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

    store = store_of(video("/a/clip.mp4"), video("/a/clip.mp4"), video("/b/clip.mp4"))

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

    store = store_of(playlist(), playlist())

    rows = item_rows(store)

    assert [(row.label, row.detail) for row in rows] == [("Folder Pair", "(1)"), ("Folder Pair", "(2)")]
    assert [(row.children[0].label, row.children[0].detail) for row in rows] == [("clip.mp4", ""), ("clip.mp4", "")]


def test_same_names_in_different_sections_get_no_location_hint(tmp_path: Path) -> None:
    (tmp_path / "site_a").mkdir()
    (tmp_path / "site_a" / "front.mp4").write_bytes(b"")
    store = store_of_items(
        VideoItem(video=tmp_path / "site_a" / "front.mp4"),
        LiveStreamItem(label="front.mp4", host="camera.local"),
    )

    live_section, videos_section = browser_sections(store)

    assert [(row.label, row.detail) for row in videos_section.rows] == [("front.mp4", "")]
    assert [(row.label, row.detail) for row in live_section.rows] == [("front.mp4", "camera.local")]


def test_same_names_in_one_section_get_location_hints_while_other_sections_do_not_count(tmp_path: Path) -> None:
    for folder in ("site_a", "site_b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "front.mp4").write_bytes(b"")
    store = store_of_items(
        VideoItem(video=tmp_path / "site_a" / "front.mp4"),
        VideoItem(video=tmp_path / "site_b" / "front.mp4"),
        LiveStreamItem(label="front.mp4", host="camera.local"),
    )

    live_section, videos_section = browser_sections(store)

    assert [(row.label, row.detail) for row in videos_section.rows] == [
        ("front.mp4", "site_a"),
        ("front.mp4", "site_b"),
    ]
    assert [(row.label, row.detail) for row in live_section.rows] == [("front.mp4", "camera.local")]


def test_unavailable_items_with_the_same_name_get_no_hints() -> None:
    store = store_of_items(
        UnreadableItem.from_raw({"kind": "future", "label": "same"}, "Unknown kind"),
        UnreadableItem.from_raw({"kind": "future", "label": "same"}, "Unknown kind"),
    )

    (other,) = browser_sections(store)

    assert [row.detail for row in other.rows] == ["", ""]


@pytest.mark.parametrize("head", [1, 2])
def test_repeated_live_sources_keep_the_address_when_numbered(head: int) -> None:
    def live() -> LiveVideoContent:
        return LiveVideoContent(
            display_name="Gate",
            source_spec=LiveRTSPStreamSpec(host="camera.local", camera_head=head, username="root", password="secret"),
        )

    rows = item_rows(store_of(live(), live()))

    address = "camera.local" if head == 1 else "camera.local · head 2"
    assert [row.detail for row in rows] == [f"{address} (1)", f"{address} (2)"]
    assert all("camera.local" in row.search_text for row in rows)


def test_same_named_pending_items_get_no_hints() -> None:
    items = [LiveStreamItem(label="Camera", host=host) for host in ("camera.local", "other.local")]

    (section,) = build_browser_sections([ItemResolution.pending(item) for item in items], lambda _ref: True)

    assert [row.detail for row in section.rows] == ["", ""]
