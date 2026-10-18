"""Tests for the transports a StreamMediaController opens and the frames it displays."""

from __future__ import annotations

import pytest

from ax_devil.modules.data_sources.live.rtsp_source import RTSPOverlayDecoder
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.workspace.core import (
    LiveMQTTOverlaySourceSpec,
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
    make_sync_result,
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
                device_api_protocol="http",
            ),
            {"broker_host": "broker.local", "broker_username": "mqtt-user", "analytics_data_source_key": "topic"},
        ),
        (
            LiveWebSocketOverlaySourceSpec(
                handler_type="LIVE", topic="com.axis.scene.frame.v1", channel_id=2, device_api_protocol="http"
            ),
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
    controller = make_controller(make_live_content(overlay_spec))

    (video,), (overlay,) = live_transports
    assert "analytics=" not in video.url
    assert (
        overlay.options.items()
        >= {
            **expected_options,
            "device_host": "camera.local",
            "device_username": "root",
            "device_password": "pass",
            "device_api_protocol": "http",
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


def test_same_named_streams_keep_independent_source_diagnostics(
    make_controller: MakeController, live_transports: LiveTransports
) -> None:
    """Closing one identically named stream must not remove another stream's observations."""
    from ax_devil.modules.diagnostics.metrics_store import get_metrics_store, set_metrics_enabled
    from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store

    set_metrics_enabled(True)
    controllers = [make_controller(make_live_content(name="Same camera")) for _ in range(2)]

    def synced_streams() -> set[str]:
        return {
            source_id
            for source_id, source in get_metrics_store().snapshot().items()
            if source.label.endswith("Same camera") and "Last synced frame" in source.observations
        }

    for controller in controllers:
        assert controller.synchronizer is not None
        controller.synchronizer.syncReady.emit(make_sync_result())
    stream_ids = synced_streams()
    assert len(stream_ids) == 2
    viewer_links = [set(viewer.source_ids) & stream_ids for viewer in get_render_metrics_store().snapshot()]
    assert sorted(len(link) for link in viewer_links if link) == [1, 1]

    controllers[0].cleanup()

    remaining = synced_streams()
    assert len(remaining) == 1 and remaining < stream_ids
