"""Tests for the spec-backed content model."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    EntryLane,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
    OverlayContent,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)


def _live_source_spec() -> LiveRTSPStreamSpec:
    return LiveRTSPStreamSpec(host="camera.local", username="root", password="pass")


def _make_video(name: str = "clip.mp4") -> SeekableVideoContent:
    return SeekableVideoContent(
        display_name=name,
        source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")),
        overlays=(),
    )


def test_content_instances_have_unique_ids_and_identity_equality() -> None:
    a = _make_video("a")
    b = _make_video("a")
    assert a.content_id != b.content_id
    assert a != b

    p1 = PlaylistContent(
        display_name="p",
        entries=(PlaylistEntry(lanes=_make_video().standalone_lanes(), default_considered=True),),
    )
    p2 = PlaylistContent(
        display_name="p",
        entries=(PlaylistEntry(lanes=_make_video().standalone_lanes(), default_considered=True),),
    )
    assert p1.content_id != p2.content_id


def test_entry_lane_rejects_overlay_incompatible_with_video() -> None:
    overlay = OverlayContent(
        display_name="MQTT",
        source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
    )

    with pytest.raises(TypeError, match="Seekable video lanes require file overlay source specs"):
        EntryLane(display_name="invalid", video=_make_video(), default_considered=True, overlay=overlay)


def test_playlist_entry_rejects_live_video_content() -> None:
    live_content = LiveVideoContent(
        display_name="camera-1",
        source_spec=_live_source_spec(),
    )

    with pytest.raises(TypeError, match="Playlist entries require seekable video content"):
        PlaylistEntry(
            lanes=(EntryLane(display_name="camera-1", video=live_content, default_considered=True),),
            default_considered=True,
        )


def test_playlist_entry_rejects_empty_lanes() -> None:
    with pytest.raises(ValueError, match="Playlist entries require at least one lane"):
        PlaylistEntry(lanes=(), default_considered=True)


def test_playlist_content_rejects_empty_entries() -> None:
    with pytest.raises(ValueError, match="Playlists require at least one entry"):
        PlaylistContent(display_name="empty", entries=())


def test_seekable_video_content_with_metadata_is_hashable() -> None:
    content = SeekableVideoContent(
        display_name="clip.mp4",
        source_spec=FileVideoSourceSpec(path=Path("/tmp/clip.mp4")),
        metadata={"path": "/tmp/clip.mp4"},
    )
    assert content in {content}


def test_overlay_source_kinds_have_display_names() -> None:
    assert OverlaySourceKind.RTSP_SOURCE.display_name == "RTSP"
    assert OverlaySourceKind.MQTT_SOURCE.display_name == "MQTT"
    assert OverlaySourceKind.WEBSOCKET_SOURCE.display_name == "DataHub WebSocket"


def test_seekable_video_rejects_live_overlay_source() -> None:
    overlay = OverlayContent(
        display_name="MQTT",
        source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
    )

    with pytest.raises(TypeError, match="Seekable video content requires file overlay source specs"):
        SeekableVideoContent(
            display_name="clip",
            source_spec=FileVideoSourceSpec(path=Path("/tmp/clip.mp4")),
            overlays=(overlay,),
        )


def test_live_video_rejects_file_overlay_source() -> None:
    overlay = OverlayContent(
        display_name="file",
        source_spec=FileOverlaySourceSpec(path=Path("/tmp/overlay.txt"), handler_type="FILE"),
    )

    with pytest.raises(TypeError, match="Live video content requires an RTSP or MQTT overlay source spec"):
        LiveVideoContent(display_name="live", source_spec=_live_source_spec(), overlays=(overlay,))


def test_live_video_rejects_multiple_overlays() -> None:
    overlays = (
        OverlayContent(
            display_name="RTSP",
            source_spec=LiveRTSPOverlaySourceSpec(handler_type="LIVE"),
        ),
        OverlayContent(
            display_name="MQTT",
            source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
        ),
    )

    with pytest.raises(ValueError, match="Live video content supports at most one overlay"):
        LiveVideoContent(display_name="live", source_spec=_live_source_spec(), overlays=overlays)


def test_live_video_content_owns_standalone_overlay_lanes() -> None:
    overlay = OverlayContent(
        display_name="MQTT",
        source_spec=LiveMQTTOverlaySourceSpec(
            handler_type="AXIS_MQTT",
            broker_host="broker.local",
            analytics_data_source_key="analytics",
        ),
        metadata={"note": "primary analytics"},
    )
    content = LiveVideoContent(
        display_name="cam-1",
        source_spec=_live_source_spec(),
        overlays=(overlay,),
        metadata={"note": "test camera"},
    )

    lanes = content.standalone_lanes()

    assert len(lanes) == 1
    assert lanes[0].display_name == "MQTT"
    assert lanes[0].source_kind == OverlaySourceKind.MQTT_SOURCE
    assert lanes[0].metadata == {}
    assert overlay.metadata == {"note": "primary analytics"}


def test_live_video_content_owns_embedded_rtsp_standalone_lane() -> None:
    overlay = OverlayContent(
        display_name="RTSP",
        source_spec=LiveRTSPOverlaySourceSpec(handler_type="AXIS_RTSP"),
        metadata={"note": "embedded analytics"},
    )
    content = LiveVideoContent(
        display_name="cam-1",
        source_spec=_live_source_spec(),
        overlays=(overlay,),
        metadata={"note": "test camera"},
    )

    lanes = content.standalone_lanes()

    assert len(lanes) == 1
    assert lanes[0].display_name == "RTSP"
    assert lanes[0].source_kind == OverlaySourceKind.RTSP_SOURCE
    assert lanes[0].metadata == {}
    assert overlay.metadata == {"note": "embedded analytics"}


def test_live_video_content_owns_websocket_standalone_lane() -> None:
    overlay = OverlayContent(
        display_name="DataHub WebSocket",
        source_spec=LiveWebSocketOverlaySourceSpec(
            handler_type="ADF_V1_FRAME",
            topic="com.axis.scene.frame.v1",
            channel_id=2,
        ),
    )
    content = LiveVideoContent(
        display_name="cam-1",
        source_spec=_live_source_spec(),
        overlays=(overlay,),
    )

    lanes = content.standalone_lanes()

    assert lanes[0].source_kind is OverlaySourceKind.WEBSOCKET_SOURCE
    assert lanes[0].display_name == "DataHub WebSocket"


def test_default_considered_is_explicit_field_not_metadata() -> None:
    overlay = OverlayContent(
        display_name="overlay",
        source_spec=FileOverlaySourceSpec(path=Path("/tmp/overlay.txt"), handler_type="MOT_FILE"),
        metadata={"note": "test overlay"},
    )
    content = SeekableVideoContent(
        display_name="clip",
        source_spec=FileVideoSourceSpec(path=Path("/tmp/clip.mp4")),
        overlays=(overlay,),
    )

    lanes = content.standalone_lanes()

    assert lanes[0].default_considered is True
    assert "default_considered" not in lanes[0].metadata


def test_content_owns_supported_consideration_items() -> None:
    overlay = OverlayContent(
        display_name="overlay",
        source_spec=FileOverlaySourceSpec(path=Path("/tmp/overlay.txt"), handler_type="MOT_FILE"),
    )
    seekable = SeekableVideoContent(
        display_name="clip",
        source_spec=FileVideoSourceSpec(path=Path("/tmp/clip.mp4")),
        overlays=(overlay,),
    )
    live = LiveVideoContent(
        display_name="live",
        source_spec=_live_source_spec(),
        overlays=(
            OverlayContent(
                display_name="RTSP",
                source_spec=LiveRTSPOverlaySourceSpec(handler_type="LIVE"),
            ),
        ),
    )

    [seekable_item] = seekable.consideration_items()

    assert seekable_item.ref == ConsiderationItemRef.video_lane(seekable.content_id, 0)
    assert seekable_item.default_considered
    assert live.consideration_items() == ()
