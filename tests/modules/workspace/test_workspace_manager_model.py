"""Tests for WorkspaceManager content facts and browser-row projection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    OnScreenWorkspaceItem,
    OverlayContent,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
    WorkspaceManager,
)
from ax_devil.modules.workspace.item_info import WorkspaceItemInfo


def _make_overlay(name: str) -> OverlayContent:
    return OverlayContent(
        display_name=name,
        source_spec=FileOverlaySourceSpec(path=Path(f"/tmp/{name}.txt"), handler_type="AXIS_JSON"),
        metadata={"note": "test overlay"},
    )


def _make_video(name: str = "test.mp4", overlay_count: int = 0) -> SeekableVideoContent:
    return SeekableVideoContent(
        display_name=name,
        source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")),
        overlays=tuple(_make_overlay(f"Overlay {index + 1}") for index in range(overlay_count)),
    )


def _make_live_video(
    name: str = "live",
    overlay_source: OverlaySourceKind = OverlaySourceKind.NO_SOURCE,
) -> LiveVideoContent:
    overlays: tuple[OverlayContent, ...] = ()
    if overlay_source is OverlaySourceKind.MQTT_SOURCE:
        overlays = (
            OverlayContent(
                display_name=OverlaySourceKind.MQTT_SOURCE.display_name,
                source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
            ),
        )
    elif overlay_source is OverlaySourceKind.RTSP_SOURCE:
        overlays = (
            OverlayContent(
                display_name=OverlaySourceKind.RTSP_SOURCE.display_name,
                source_spec=LiveRTSPOverlaySourceSpec(handler_type="LIVE"),
                metadata={"note": "test overlay"},
            ),
        )
    return LiveVideoContent(
        display_name=name,
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass"),
        overlays=overlays,
        metadata={"note": "test camera"},
    )


def _make_playlist(name: str = "Suite") -> PlaylistContent:
    return PlaylistContent(
        display_name=name,
        entries=(
            PlaylistEntry(lanes=_make_video("a.mp4").standalone_lanes(), default_considered=True),
            PlaylistEntry(lanes=_make_video("b.mp4").standalone_lanes(), default_considered=True),
        ),
    )


def test_remove_equal_content_instance_clears_consideration_state() -> None:
    state = WorkspaceManager()
    video = _make_video("test.mp4", overlay_count=2)
    state.add_content(video)
    lane_ref = ConsiderationItemRef.video_lane(video.content_id, 1)
    state.set_item_considered(lane_ref, False)
    equal_copy = SeekableVideoContent(
        display_name=video.display_name,
        source_spec=video.source_spec,
        overlays=video.overlays,
        content_id=video.content_id,
    )

    state.remove_content(equal_copy)
    assert state.is_item_considered(lane_ref)


def test_open_items_are_inputs_to_browser_projection() -> None:
    state = WorkspaceManager()
    playlist = _make_playlist()
    state.add_content(playlist)
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

    first_rows = state.get_browser_rows(first_open_item)
    second_rows = state.get_browser_rows(second_open_item)

    assert first_rows[0].children[0].is_open
    assert not first_rows[0].children[1].is_open
    assert not second_rows[0].children[0].is_open
    assert second_rows[0].children[1].is_open


def test_consideration_defaults_and_updates_are_owned_by_manager() -> None:
    state = WorkspaceManager()
    video = _make_video("a.mp4")
    lane = video.standalone_lanes()[0]
    playlist = PlaylistContent(
        display_name="Suite",
        entries=(
            PlaylistEntry(lanes=(lane,), default_considered=False),
            PlaylistEntry(
                lanes=(
                    lane,
                    lane.__class__(
                        display_name=lane.display_name,
                        video=lane.video,
                        default_considered=False,
                        overlay=lane.overlay,
                    ),
                ),
                default_considered=True,
            ),
        ),
    )

    state.add_content(playlist)

    entry_ref = ConsiderationItemRef.playlist_entry(playlist.content_id, 0)
    lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 1, 1)
    assert not state.is_item_considered(entry_ref)
    assert not state.is_item_considered(lane_ref)

    state.set_item_considered(entry_ref, True)
    assert state.is_item_considered(entry_ref)
    state.set_item_considered(entry_ref, True)


def test_invalid_lane_ref_does_not_affect_valid_lanes() -> None:
    state = WorkspaceManager()
    playlist = _make_playlist()
    state.add_content(playlist)

    invalid_lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 0, 99)
    state.set_item_considered(invalid_lane_ref, False)

    valid_lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 0, 0)
    assert state.is_item_considered(valid_lane_ref)


def test_unknown_refs_are_ignored() -> None:
    state = WorkspaceManager()
    lane_ref = ConsiderationItemRef.video_lane("nonexistent", 0)

    state.set_item_considered(lane_ref, False)

    assert state.is_item_considered(lane_ref)


def test_video_rows_include_overlay_children_targets_refs_and_information() -> None:
    state = WorkspaceManager()
    video = _make_video("test.mp4", overlay_count=2)
    state.add_content(video)
    state.set_item_considered(ConsiderationItemRef.video_lane(video.content_id, 1), False)
    open_items = frozenset({OnScreenWorkspaceItem(kind="video", content_id=video.content_id)})

    row = state.get_browser_rows(open_items)[0]

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
    state = WorkspaceManager()
    video = _make_video("test.mp4", overlay_count=2)
    state.add_content(video)
    information = WorkspaceItemInfo(title="test.mp4", fields=(("type", "seekable video"),))

    with patch(
        "ax_devil.modules.workspace.workspace_manager.build_content_information",
        return_value=information,
    ) as build_information:
        row = state.get_browser_rows()[0]

        build_information.assert_not_called()
        information_factory = row.information_factory
        assert information_factory is not None
        assert information_factory() == information

    build_information.assert_called_once_with(video)


def test_live_rows_use_live_icon_and_embedded_overlay_child() -> None:
    state = WorkspaceManager()
    live_video = _make_live_video(overlay_source=OverlaySourceKind.RTSP_SOURCE)
    state.add_content(live_video)

    row = state.get_browser_rows()[0]

    assert row.icon_kind == "live_video"
    assert [child.label for child in row.children] == ["RTSP"]
    assert row.children[0].consideration_ref is None


def test_playlist_rows_include_entries_lanes_open_flags_and_information_payloads() -> None:
    state = WorkspaceManager()
    playlist = PlaylistContent(
        display_name="Suite",
        metadata={"resolver": "Synthetic"},
        entries=(
            PlaylistEntry(
                lanes=_make_video("a.mp4", overlay_count=2).standalone_lanes(),
                default_considered=True,
                metadata={"note": "first entry"},
            ),
            PlaylistEntry(lanes=_make_video("b.mp4").standalone_lanes(), default_considered=True),
        ),
    )
    state.add_content(playlist)
    open_items = frozenset(
        {OnScreenWorkspaceItem(kind="playlist_entry", content_id=playlist.content_id, entry_index=1)}
    )

    row = state.get_browser_rows(open_items)[0]
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
    state = WorkspaceManager()
    playlist = PlaylistContent(
        display_name="Suite",
        entries=(
            PlaylistEntry(
                lanes=(
                    _make_video("a.mp4").standalone_lanes()[0],
                    _make_video("b.mp4").standalone_lanes()[0],
                ),
                default_considered=True,
            ),
        ),
    )
    state.add_content(playlist)

    row = state.get_browser_rows()[0]

    assert row.children[0].icon_kind == "playlist"


def test_live_content_logs_and_representations_exclude_credentials(caplog: pytest.LogCaptureFixture) -> None:
    """Removal warnings and nested source representations never expose credentials."""
    stream_url = "rtsp://synthetic-user:synthetic-device-secret@camera.local/stream?token=synthetic-token"
    content = LiveVideoContent(
        display_name=stream_url,
        source_spec=LiveRTSPStreamSpec(
            host="camera.local", username="synthetic-user", password="synthetic-device-secret", stream_url=stream_url
        ),
        overlays=(
            OverlayContent(
                "MQTT",
                LiveMQTTOverlaySourceSpec(
                    handler_type="LIVE",
                    broker_host="broker.local",
                    broker_username="synthetic-broker-user",
                    broker_password="synthetic-broker-secret",
                ),
            ),
        ),
    )
    manager = WorkspaceManager()
    with caplog.at_level("DEBUG"):
        manager.add_content(content)
        manager.remove_content(content)
        manager.remove_content(content)
    assert content.content_id in caplog.text
    assert "not in workspace" in caplog.text
    representations = f"{content.source_spec!r} {content.overlays!r}"
    for secret in (
        stream_url,
        "synthetic-user",
        "synthetic-device-secret",
        "synthetic-broker-user",
        "synthetic-broker-secret",
        "synthetic-token",
    ):
        assert secret not in caplog.text
        assert secret not in representations


def test_same_named_rows_get_shortest_distinguishing_folder_hint() -> None:
    def video(path: str) -> SeekableVideoContent:
        return SeekableVideoContent(display_name=Path(path).name, source_spec=FileVideoSourceSpec(path=Path(path)))

    manager = WorkspaceManager()
    manager.add_contents(
        [
            video("/data/site_a/parking_lot_cam3.mp4"),
            video("/data/site_b/parking_lot_cam3.mp4"),
            video("/data/site_c/day1/gate.mp4"),
            video("/data/site_d/day1/gate.mp4"),
            video("/data/site_a/unique.mp4"),
        ]
    )

    rows = manager.get_browser_rows()

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
    manager = WorkspaceManager()
    manager.add_content(playlist)

    (row,) = manager.get_browser_rows()

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
    manager = WorkspaceManager()
    if in_playlist:
        manager.add_content(
            PlaylistContent(
                display_name="Comparison",
                entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
            )
        )
        lane_rows = manager.get_browser_rows()[0].children[0].children
    else:
        manager.add_content(video)
        lane_rows = manager.get_browser_rows()[0].children

    assert [row.display_text for row in lane_rows] == ["tracks.txt — detector_a", "tracks.txt — detector_b"]
    assert [row.tooltip for row in lane_rows] == ["/detector_a/tracks.txt", "/detector_b/tracks.txt"]


def test_same_named_rows_in_one_folder_fall_back_to_file_names() -> None:
    manager = WorkspaceManager()
    manager.add_contents(
        [
            SeekableVideoContent(display_name="Camera", source_spec=FileVideoSourceSpec(path=Path("/clips/front.mp4"))),
            SeekableVideoContent(display_name="Camera", source_spec=FileVideoSourceSpec(path=Path("/clips/rear.mp4"))),
        ]
    )

    assert [row.display_text for row in manager.get_browser_rows()] == ["Camera — front.mp4", "Camera — rear.mp4"]


def test_same_named_live_streams_are_told_apart_by_host_and_camera_head() -> None:
    def live(host: str, head: int) -> LiveVideoContent:
        return LiveVideoContent(
            display_name="Live",
            source_spec=LiveRTSPStreamSpec(host=host, username="root", password="secret", camera_head=head),
        )

    manager = WorkspaceManager()
    manager.add_contents([live("camera", 1), live("camera", 2), live("other", 1)])

    rows = manager.get_browser_rows()

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

    manager = WorkspaceManager()
    manager.add_contents([video("/a/clip.mp4"), video("/a/clip.mp4"), video("/b/clip.mp4")])

    assert [row.display_text for row in manager.get_browser_rows()] == [
        "clip.mp4 — a (1)",
        "clip.mp4 — a (2)",
        "clip.mp4 — b",
    ]


def test_same_named_rows_without_locations_are_numbered() -> None:
    def playlist() -> PlaylistContent:
        video = SeekableVideoContent(display_name="clip.mp4", source_spec=FileVideoSourceSpec(path=Path("/a/clip.mp4")))
        entry = PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True)
        return PlaylistContent(display_name="Folder Pair", entries=(entry,))

    manager = WorkspaceManager()
    manager.add_contents([playlist(), playlist()])

    rows = manager.get_browser_rows()

    assert [row.display_text for row in rows] == ["Folder Pair — (1)", "Folder Pair — (2)"]
    assert [row.children[0].display_text for row in rows] == ["clip.mp4", "clip.mp4"]
