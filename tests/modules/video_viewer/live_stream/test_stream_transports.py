"""Tests for the transports a StreamMediaController opens and the frames it displays."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ax_devil.core.device_endpoint import DeviceEndpoint, DeviceProtocol
from ax_devil.modules.data_sources.live.rtsp_source import RTSPOverlayDecoder
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.workspace.core import (
    LiveMQTTOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveWebSocketOverlaySourceSpec,
)
from tests.helpers.live_stream import (
    MQTT_OVERLAY,
    RTSP_OVERLAY,
    FakeClock,
    LiveTransports,
    MakeController,
    OverlaySpec,
    displayed_ids,
    make_frame,
    make_live_content,
    make_overlay,
)


def test_embedded_rtsp_overlay_requests_analytics_on_the_video_stream(
    make_controller: MakeController, live_transports: LiveTransports
) -> None:
    controller = make_controller(make_live_content(RTSP_OVERLAY))

    (video,), overlays = live_transports
    assert overlays == []
    assert "analytics=" in video.url
    overlay = video.options["overlay"]
    assert isinstance(overlay, RTSPOverlayDecoder)
    actual: tuple[object, ...] = (overlay.decoder, overlay.handler_type, overlay.filter_factory)
    assert actual == ("decoder:LIVE", "LIVE", "filters:LIVE")
    assert controller.overlay_source is video
    assert controller.get_filter_config() is video.filter_config


@pytest.mark.parametrize(
    ("overlay_spec", "expected_options"),
    [
        (
            LiveMQTTOverlaySourceSpec(
                handler_type="LIVE",
                broker_host="broker.local",
                broker_username="mqtt-user",
                analytics_data_source_key="topic",
            ),
            {"broker_host": "broker.local", "broker_username": "mqtt-user", "analytics_data_source_key": "topic"},
        ),
        (
            LiveWebSocketOverlaySourceSpec(handler_type="LIVE", topic="com.axis.scene.frame.v1", channel_id=2),
            {"topic": "com.axis.scene.frame.v1", "channel_id": 2},
        ),
    ],
    ids=["mqtt", "websocket"],
)
def test_separate_overlay_spec_opens_its_own_transport_with_device_settings(
    make_controller: MakeController,
    live_transports: LiveTransports,
    overlay_spec: OverlaySpec,
    expected_options: dict[str, object],
) -> None:
    stream = LiveRTSPStreamSpec(
        host="camera.local",
        username="root",
        password="pass",
        device_api_protocol=DeviceProtocol.HTTP,
        device_port=8080,
        rtsp_port=8554,
        connection_timeout_s=7,
    )
    controller = make_controller(replace(make_live_content(overlay_spec), source_spec=stream))

    (video,), (overlay,) = live_transports
    assert "analytics=" not in video.url
    assert video.url.startswith("rtsp://root:pass@camera.local:8554/")
    assert video.options["timeout_s"] == 7
    assert (
        overlay.options.items()
        >= {
            **expected_options,
            "device": DeviceEndpoint("camera.local", DeviceProtocol.HTTP, 8080),
            "device_username": "root",
            "device_password": "pass",
            "timeout_s": 7,
            "decoder": "decoder:LIVE",
            "handler_type": "LIVE",
            "filter_factory": "filters:LIVE",
        }.items()
    )
    assert controller.overlay_source is overlay
    assert controller.get_filter_config() is overlay.filter_config


@pytest.mark.parametrize("overlay_spec", [None, RTSP_OVERLAY, MQTT_OVERLAY], ids=["none", "rtsp", "mqtt"])
def test_live_frames_reach_the_display_with_the_matching_overlay(
    make_controller: MakeController,
    live_transports: LiveTransports,
    clock: FakeClock,
    overlay_spec: OverlaySpec | None,
) -> None:
    """Frames and overlays from the configured transports are synchronized before display."""
    displayed: list[VideoFrameWithOverlays] = []
    controller = make_controller(make_live_content(overlay_spec), frame_displayed=displayed.append)
    videos, overlays = live_transports
    controller.start_playback()

    (overlays or videos)[0].overlayReady.emit(make_overlay(1))
    videos[0].frameReady.emit(make_frame(1))
    clock.now = 10.0
    videos[0].frameReady.emit(make_frame(2))

    assert displayed_ids(displayed) == [1]
    overlay = displayed[0].overlays
    if overlay_spec is None:
        assert overlay is None
    else:
        assert overlay is not None and overlay.overlay_id == 1


def test_paused_stream_ignores_frames_and_resume_discards_frames_buffered_before_the_pause(
    make_controller: MakeController, live_transports: LiveTransports, clock: FakeClock
) -> None:
    displayed: list[VideoFrameWithOverlays] = []
    controller = make_controller(make_live_content(), frame_displayed=displayed.append)
    (video,), _overlays = live_transports
    controller.start_playback()
    video.frameReady.emit(make_frame(1))

    controller.pause_playback()
    clock.now = 10.0
    video.frameReady.emit(make_frame(2))

    assert controller.is_paused and video.pauses == 1
    assert displayed == []

    controller.resume_playback()
    video.frameReady.emit(make_frame(10))
    clock.now = 20.0
    video.frameReady.emit(make_frame(20))

    assert displayed_ids(displayed) == [10]


@pytest.mark.parametrize("overlay_spec", [RTSP_OVERLAY, MQTT_OVERLAY], ids=["rtsp", "mqtt"])
def test_cleanup_releases_every_transport_exactly_once(
    make_controller: MakeController, live_transports: LiveTransports, overlay_spec: OverlaySpec
) -> None:
    controller = make_controller(make_live_content(overlay_spec))
    controller.start_playback()

    controller.cleanup()
    controller.cleanup()

    videos, overlays = live_transports
    assert [(transport.stops, transport.deletes) for transport in videos + overlays] == [(1, 1)] * len(
        videos + overlays
    )
