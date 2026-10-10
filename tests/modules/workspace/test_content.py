"""Tests for the spec-backed content model."""

from __future__ import annotations

from collections.abc import Callable
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
from ax_devil.modules.workspace.content import LiveOverlaySourceSpec
from ax_devil.modules.workspace.item_info import build_video_information


def _live_source_spec() -> LiveRTSPStreamSpec:
    return LiveRTSPStreamSpec(host="camera.local", username="root", password="pass")


def _make_video(name: str = "clip.mp4") -> SeekableVideoContent:
    return SeekableVideoContent(
        display_name=name,
        source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")),
        overlays=(),
    )


def _mqtt_overlay() -> OverlayContent:
    return OverlayContent(
        display_name="MQTT",
        source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
    )


def _file_overlay() -> OverlayContent:
    return OverlayContent(
        display_name="file",
        source_spec=FileOverlaySourceSpec(path=Path("/tmp/overlay.txt"), handler_type="MOT_FILE"),
    )


def test_content_instances_have_unique_ids_and_identity_equality() -> None:
    a = SeekableVideoContent(
        display_name="a",
        source_spec=FileVideoSourceSpec(path=Path("/tmp/a")),
        metadata={"path": "/tmp/a"},
    )
    b = _make_video("a")
    assert a.content_id != b.content_id
    assert a != b
    assert a in {a}

    p1 = PlaylistContent(
        display_name="p",
        entries=(PlaylistEntry(lanes=_make_video().standalone_lanes(), default_considered=True),),
    )
    p2 = PlaylistContent(
        display_name="p",
        entries=(PlaylistEntry(lanes=_make_video().standalone_lanes(), default_considered=True),),
    )
    assert p1.content_id != p2.content_id


@pytest.mark.parametrize(
    ("build", "error", "match"),
    [
        pytest.param(
            lambda: EntryLane(display_name="x", video=_make_video(), default_considered=True, overlay=_mqtt_overlay()),
            TypeError,
            "Seekable video lanes require file overlay",
            id="seekable-lane-with-live-overlay",
        ),
        pytest.param(
            lambda: SeekableVideoContent(
                display_name="clip",
                source_spec=FileVideoSourceSpec(path=Path("/tmp/c.mp4")),
                overlays=(_mqtt_overlay(),),
            ),
            TypeError,
            "Seekable video content requires file overlay",
            id="seekable-video-with-live-overlay",
        ),
        pytest.param(
            lambda: LiveVideoContent(display_name="live", source_spec=_live_source_spec(), overlays=(_file_overlay(),)),
            TypeError,
            "Live video content requires",
            id="live-video-with-file-overlay",
        ),
        pytest.param(
            lambda: LiveVideoContent(
                display_name="live", source_spec=_live_source_spec(), overlays=(_mqtt_overlay(), _mqtt_overlay())
            ),
            ValueError,
            "at most one overlay",
            id="live-video-with-two-overlays",
        ),
        pytest.param(
            lambda: PlaylistEntry(
                lanes=(
                    EntryLane(
                        display_name="cam",
                        video=LiveVideoContent(display_name="cam", source_spec=_live_source_spec()),
                        default_considered=True,
                    ),
                ),
                default_considered=True,
            ),
            TypeError,
            "Playlist entries require seekable video",
            id="playlist-entry-with-live-video",
        ),
        pytest.param(
            lambda: PlaylistEntry(lanes=(), default_considered=True),
            ValueError,
            "at least one lane",
            id="playlist-entry-without-lanes",
        ),
        pytest.param(
            lambda: PlaylistContent(display_name="empty", entries=()),
            ValueError,
            "at least one entry",
            id="playlist-without-entries",
        ),
    ],
)
def test_content_rejects_unsupported_shapes(build: Callable[[], object], error: type[Exception], match: str) -> None:
    with pytest.raises(error, match=match):
        build()


@pytest.mark.parametrize(
    ("source_spec", "expected_kind", "expected_name"),
    [
        pytest.param(
            LiveMQTTOverlaySourceSpec(
                handler_type="AXIS_MQTT", broker_host="broker.local", analytics_data_source_key="analytics"
            ),
            OverlaySourceKind.MQTT_SOURCE,
            "MQTT",
            id="mqtt",
        ),
        pytest.param(
            LiveRTSPOverlaySourceSpec(handler_type="AXIS_RTSP"), OverlaySourceKind.RTSP_SOURCE, "RTSP", id="rtsp"
        ),
        pytest.param(
            LiveWebSocketOverlaySourceSpec(handler_type="ADF_V1_FRAME", topic="com.axis.scene.frame.v1", channel_id=2),
            OverlaySourceKind.WEBSOCKET_SOURCE,
            "DataHub WebSocket",
            id="websocket",
        ),
    ],
)
def test_live_content_projects_overlay_into_standalone_lane(
    source_spec: LiveOverlaySourceSpec, expected_kind: OverlaySourceKind, expected_name: str
) -> None:
    """Live lanes inherit their overlay kind and label while keeping metadata on its owner."""
    overlay = OverlayContent(display_name=expected_name, source_spec=source_spec, metadata={"note": "analytics"})
    content = LiveVideoContent(display_name="cam-1", source_spec=_live_source_spec(), overlays=(overlay,))

    [lane] = content.standalone_lanes()

    assert lane.display_name == expected_name
    assert lane.source_kind is expected_kind
    assert lane.metadata == {}
    assert lane.overlay is overlay


def test_seekable_video_lane_is_considered_by_default_and_live_has_no_items() -> None:
    seekable = SeekableVideoContent(
        display_name="clip",
        source_spec=FileVideoSourceSpec(path=Path("/tmp/clip.mp4")),
        overlays=(_file_overlay(),),
    )
    live = LiveVideoContent(
        display_name="live",
        source_spec=_live_source_spec(),
        overlays=(OverlayContent(display_name="RTSP", source_spec=LiveRTSPOverlaySourceSpec(handler_type="LIVE")),),
    )

    [seekable_item] = seekable.consideration_items()

    assert seekable_item.ref == ConsiderationItemRef.video_lane(seekable.content_id, 0)
    assert seekable_item.default_considered
    assert live.consideration_items() == ()


def test_live_information_never_shows_passwords() -> None:
    mqtt = LiveMQTTOverlaySourceSpec(
        handler_type="LIVE", broker_host="broker.local", broker_username="mqtt-user", broker_password="mqtt-secret"
    )
    websocket = LiveWebSocketOverlaySourceSpec(handler_type="LIVE", topic="com.axis.scene.frame.v1")
    for overlay_spec in (mqtt, websocket):
        video = LiveVideoContent(
            display_name="Live",
            source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="camera-secret"),
            overlays=(OverlayContent(display_name="Overlay", source_spec=overlay_spec),),
        )
        info = build_video_information(video)

        shown = [value for item in (info, *info.children) for _label, value in item.fields]
        assert "camera.local" in shown and "root" in shown
        assert not any("secret" in value for value in shown)
