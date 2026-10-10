"""Tests for StreamMediaController with spec-backed LiveVideoContent."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from ax_devil_rtsp import StreamConfig
from PySide6.QtCore import QCoreApplication, QThread
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.base import FrameSource, OverlaySource
from ax_devil.modules.data_sources.live.rtsp_source import RTSPOverlayDecoder
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.synchronization import SyncResult, TimestampedData
from ax_devil.modules.synchronization.qt_adapter import QtStreamSync
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer import stream_media_controller
from ax_devil.modules.video_viewer.live_connection import LiveConnectionState, LiveConnectionStatus, LiveFeed
from ax_devil.modules.video_viewer.stream_media_controller import StreamMediaController
from ax_devil.modules.workspace.core import (
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
    OverlayContent,
)
from tests.helpers.scene_inspector import RecordingSceneInspector


class _FrameSource(FrameSource):
    """Frame source test double."""

    def play(self) -> bool:
        return True

    def pause(self) -> None:
        return

    def stop(self) -> None:
        return

    def wait(self, timeout: int = 2000) -> bool:
        return True


def _live_content(name: str = "cam-1", stream_url: str = "rtsp://camera.local/axis") -> LiveVideoContent:
    return LiveVideoContent(
        display_name=name,
        source_spec=LiveRTSPStreamSpec(
            host="camera.local",
            username="root",
            password="pass",
            stream_url=stream_url,
        ),
    )


def _build_sync_result() -> SyncResult[FrameData, OverlayData]:
    frame_id = FrameIdentifier(sequence_id=7, timestamp_monotime_us=7_000_000.0)
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0)
    frame = FrameData(frame_id=frame_id, content=image, source_id="video")
    overlay = OverlayData(
        frame_id=frame_id,
        content=Scene(time_slice=TimeSlice(start=0, end=1)),
        source_id="overlay",
        metadata={"source": "scene-decoder"},
    )
    return SyncResult(
        frame=TimestampedData(data=frame, capture_time=7.0, arrival_time=7.0),
        overlay=TimestampedData(data=overlay, capture_time=7.0, arrival_time=7.0),
    )


def test_live_controller_uses_qt_stream_sync_and_displays_presented_frame(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    displayed_frames: list[VideoFrameWithOverlays] = []
    display = FrameDisplay()
    qtbot.addWidget(display)
    content = _live_content()
    monkeypatch.setattr(
        "ax_devil.modules.video_viewer.stream_media_controller.RTSPSource", lambda _url, **_kwargs: _FrameSource()
    )
    controller = StreamMediaController(
        display,
        content,
        render_catalog_selection=render_catalog_manager.create_selection(),
        frame_displayed=displayed_frames.append,
    )

    assert isinstance(controller.synchronizer, QtStreamSync)

    controller._on_sync_result(_build_sync_result())

    assert len(displayed_frames) == 1
    displayed = displayed_frames[0]
    assert displayed.frame.frame_id == 7
    assert displayed.overlays is not None
    assert displayed.overlays.overlay_id == 7
    assert displayed.overlays.metadata == {
        "source": "scene-decoder",
        "overlay_reused": False,
        "overlay_opacity": 1.0,
    }

    controller.cleanup()


def test_live_controller_displays_frame_before_scheduling_scene_inspection(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    inspector = RecordingSceneInspector()

    def record_displayed(_frame: VideoFrameWithOverlays) -> None:
        assert inspector.updates == []

    display = FrameDisplay()
    qtbot.addWidget(display)
    monkeypatch.setattr(
        "ax_devil.modules.video_viewer.stream_media_controller.RTSPSource", lambda _url, **_kwargs: _FrameSource()
    )
    controller = StreamMediaController(
        display,
        _live_content(),
        render_catalog_selection=render_catalog_manager.create_selection(),
        frame_displayed=record_displayed,
    )
    controller.attach_scene_inspector(inspector)

    controller._on_sync_result(_build_sync_result())

    assert inspector.cleared is True
    assert inspector.updates == []
    qtbot.waitUntil(lambda: len(inspector.updates) == 1)
    inspected_scene, inspected_frame_id, inspected_metadata = inspector.updates[0]
    assert inspected_scene is not None
    assert inspected_frame_id == FrameIdentifier(sequence_id=7, timestamp_monotime_us=7_000_000.0)
    assert inspected_metadata == {
        "source": "scene-decoder",
        "overlay_reused": False,
        "overlay_opacity": 1.0,
    }

    controller.cleanup()


def test_live_controller_enables_embedded_rtsp_overlay(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    opened: dict[str, object] = {}
    rtsp_options: dict[str, object] = {}

    class _CombinedSource(FrameSource, OverlaySource):
        def __init__(self, rtsp_url: str, **kwargs: object) -> None:
            super().__init__()
            opened["rtsp_url"] = rtsp_url
            opened.update(kwargs)

        def play(self) -> bool:
            return True

        def pause(self) -> None:
            return

        def stop(self) -> None:
            return

        def wait(self, timeout: int = 2000) -> bool:
            return True

        @property
        def handler_type(self) -> str:
            return str(opened["handler_type"])

        def get_filter_config(self) -> None:
            return None

    display = FrameDisplay()
    qtbot.addWidget(display)
    content = LiveVideoContent(
        display_name="cam-1",
        source_spec=LiveRTSPStreamSpec(
            host="camera.local",
            username="root",
            password="pass",
        ),
        overlays=(
            OverlayContent(
                display_name="RTSP",
                source_spec=LiveRTSPOverlaySourceSpec(handler_type="LIVE"),
            ),
        ),
    )
    monkeypatch.setattr("ax_devil.modules.video_viewer.stream_media_controller.RTSPSource", _CombinedSource)
    monkeypatch.setattr(
        "ax_devil_rtsp.build_axis_rtsp_url",
        lambda host, config, **kwargs: rtsp_options.update(config=config, **kwargs) or "rtsp://camera.local/axis",
    )
    decoder = MagicMock()
    filter_factory = MagicMock()
    monkeypatch.setattr(stream_media_controller, "get_payload_decoder", lambda handler_type: decoder)
    monkeypatch.setattr(stream_media_controller, "get_payload_filter_factory", lambda handler_type: filter_factory)

    controller = StreamMediaController(
        display,
        content,
        render_catalog_selection=render_catalog_manager.create_selection(),
    )

    assert opened["rtsp_url"] == "rtsp://camera.local/axis"
    overlay = opened["overlay"]
    assert isinstance(overlay, RTSPOverlayDecoder)
    assert overlay.decoder is decoder
    assert overlay.handler_type == "LIVE"
    assert overlay.filter_factory is filter_factory
    assert controller.overlay_source is controller._sources[0]
    assert controller._sources == [controller.video_source]
    config = rtsp_options["config"]
    assert isinstance(config, StreamConfig) and config.metadata

    controller.cleanup()


def test_live_controller_opens_mqtt_overlay_source_from_spec(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    opened: dict[str, object] = {}
    rtsp_options: dict[str, object] = {}

    class _OverlaySource(OverlaySource):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            opened.update(kwargs)

        def play(self) -> bool:
            return True

        def pause(self) -> None:
            return

        def stop(self) -> None:
            return

        def wait(self, timeout: int = 2000) -> bool:
            return True

        def deleteLater(self) -> None:
            return

        @property
        def handler_type(self) -> str:
            return str(opened["handler_type"])

        def get_filter_config(self) -> None:
            return None

    display = FrameDisplay()
    qtbot.addWidget(display)
    content = LiveVideoContent(
        display_name="cam-1",
        source_spec=LiveRTSPStreamSpec(
            host="camera.local",
            username="root",
            password="pass",
        ),
        overlays=(
            OverlayContent(
                display_name="MQTT",
                source_spec=LiveMQTTOverlaySourceSpec(
                    handler_type="LIVE",
                    broker_host="broker.local",
                    broker_username="mqtt-user",
                    analytics_data_source_key="topic",
                    device_api_protocol="https",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        "ax_devil.modules.video_viewer.stream_media_controller.RTSPSource", lambda _url, **_kwargs: _FrameSource()
    )
    monkeypatch.setattr("ax_devil.modules.data_sources.live.mqtt_overlay_source.MQTTOverlaySource", _OverlaySource)
    monkeypatch.setattr(
        "ax_devil_rtsp.build_axis_rtsp_url",
        lambda host, config, **kwargs: rtsp_options.update(config=config, **kwargs) or "rtsp://camera.local/axis",
    )
    monkeypatch.setattr(stream_media_controller, "get_payload_decoder", lambda handler_type: f"decoder:{handler_type}")
    monkeypatch.setattr(stream_media_controller, "get_payload_filter_factory", lambda handler_type: None)

    controller = StreamMediaController(
        display,
        content,
        render_catalog_selection=render_catalog_manager.create_selection(),
    )

    assert opened["broker_host"] == "broker.local"
    assert opened["broker_username"] == "mqtt-user"
    assert opened["device_host"] == "camera.local"
    assert opened["device_api_protocol"] == "https"
    assert opened["analytics_data_source_key"] == "topic"
    assert opened["decoder"] == "decoder:LIVE"
    assert opened["handler_type"] == "LIVE"
    config = rtsp_options["config"]
    assert isinstance(config, StreamConfig) and not config.metadata

    controller.cleanup()


def test_live_controller_opens_websocket_overlay_source_from_spec(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    opened: dict[str, object] = {}

    class _OverlaySource(OverlaySource):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            opened.update(kwargs)

        def play(self) -> bool:
            return True

        def pause(self) -> None:
            return

        def stop(self) -> None:
            return

        def wait(self, timeout: int = 2000) -> bool:
            return True

        def deleteLater(self) -> None:
            return

        @property
        def handler_type(self) -> str:
            return str(opened["handler_type"])

        def get_filter_config(self) -> None:
            return None

    display = FrameDisplay()
    qtbot.addWidget(display)
    content = LiveVideoContent(
        display_name="cam-1",
        source_spec=LiveRTSPStreamSpec(
            host="camera.local",
            username="root",
            password="pass",
        ),
        overlays=(
            OverlayContent(
                display_name="DataHub WebSocket",
                source_spec=LiveWebSocketOverlaySourceSpec(
                    handler_type="LIVE",
                    topic="com.axis.scene.frame.v1",
                    channel_id=2,
                    device_api_protocol="http",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        "ax_devil.modules.video_viewer.stream_media_controller.RTSPSource", lambda _url, **_kwargs: _FrameSource()
    )
    monkeypatch.setattr(
        "ax_devil.modules.data_sources.live.websocket_overlay_source.WebSocketOverlaySource", _OverlaySource
    )
    decoder = MagicMock()
    filter_factory = MagicMock()
    monkeypatch.setattr(stream_media_controller, "get_payload_decoder", lambda handler_type: decoder)
    monkeypatch.setattr(stream_media_controller, "get_payload_filter_factory", lambda handler_type: filter_factory)

    controller = StreamMediaController(
        display,
        content,
        render_catalog_selection=render_catalog_manager.create_selection(),
    )

    assert opened == {
        "topic": "com.axis.scene.frame.v1",
        "channel_id": 2,
        "device_host": "camera.local",
        "device_username": "root",
        "device_password": "pass",
        "device_api_protocol": "http",
        "decoder": decoder,
        "handler_type": "LIVE",
        "filter_factory": filter_factory,
    }
    assert controller.overlay_source is not None
    assert controller._sources == [controller.video_source, controller.overlay_source]

    controller.cleanup()


@pytest.mark.parametrize("mode", ["none", "rtsp", "mqtt"])
def test_live_input_routing_and_unique_lifecycle(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
    mode: str,
) -> None:
    """Use the real RTSP source contract to verify routing and exactly-once lifecycle."""
    from contextlib import ExitStack

    from ax_devil.modules.data_sources.live import rtsp_source
    from ax_devil.modules.data_sources.live.rtsp_source import RTSPSource
    from ax_devil.modules.filtering import build_default_filter_config

    transport = MagicMock()
    monkeypatch.setattr(rtsp_source, "StreamSession", transport)
    mqtt = _LiveOverlaySource()
    mqtt_filter = build_default_filter_config()
    monkeypatch.setattr(mqtt, "get_filter_config", lambda: mqtt_filter)
    monkeypatch.setattr(
        "ax_devil.modules.data_sources.live.mqtt_overlay_source.MQTTOverlaySource", lambda **_kwargs: mqtt
    )
    decoder = MagicMock()
    rtsp_filter = build_default_filter_config()
    monkeypatch.setattr(stream_media_controller, "get_payload_decoder", lambda _handler: decoder)
    monkeypatch.setattr(stream_media_controller, "get_payload_filter_factory", lambda _handler: lambda: rtsp_filter)
    overlay_specs: dict[str, LiveRTSPOverlaySourceSpec | LiveMQTTOverlaySourceSpec] = {
        "rtsp": LiveRTSPOverlaySourceSpec(handler_type="test"),
        "mqtt": LiveMQTTOverlaySourceSpec(handler_type="test", broker_host="broker.local"),
    }
    content = LiveVideoContent(
        display_name="camera",
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="", password=""),
        overlays=(OverlayContent(display_name=mode, source_spec=overlay_specs[mode]),) if mode != "none" else (),
    )
    display = FrameDisplay()
    qtbot.addWidget(display)
    controller = StreamMediaController(
        display, content, render_catalog_selection=render_catalog_manager.create_selection()
    )
    source = controller.video_source
    assert isinstance(source, RTSPSource)
    assert (transport.call_args.kwargs["on_metadata"] is not None) == (mode == "rtsp")
    assert controller._sources == ([source, mqtt] if mode == "mqtt" else [source])
    expected_filter = {"none": None, "rtsp": rtsp_filter, "mqtt": mqtt_filter}[mode]
    assert controller.get_filter_config() is expected_filter
    assert controller.overlay_source is {"none": None, "rtsp": source, "mqtt": mqtt}[mode]
    with ExitStack() as stack:
        play = stack.enter_context(patch.object(source, "play", return_value=True))
        pause = stack.enter_context(patch.object(source, "pause"))
        stop = stack.enter_context(patch.object(source, "stop", wraps=source.stop))
        delete = stack.enter_context(patch.object(source, "deleteLater"))
        mqtt_pause = stack.enter_context(patch.object(mqtt, "pause", wraps=mqtt.pause))
        mqtt_stop = stack.enter_context(patch.object(mqtt, "stop", wraps=mqtt.stop))
        mqtt_delete = stack.enter_context(patch.object(mqtt, "deleteLater", wraps=mqtt.deleteLater))
        log_error = stack.enter_context(patch.object(controller._logger, "error"))
        assert controller.synchronizer is not None
        push_overlay = stack.enter_context(patch.object(controller.synchronizer, "push_overlay"))
        push_frame = stack.enter_context(patch.object(controller.synchronizer, "push_frame"))
        reset_sync = stack.enter_context(
            patch.object(controller.synchronizer, "reset", wraps=controller.synchronizer.reset)
        )
        packet = OverlayData(
            frame_id=FrameIdentifier(sequence_id=1, timestamp_monotime_us=2_000_000),
            content=Scene(time_slice=TimeSlice(start=0, end=1)),
            source_id="rtsp",
        )
        source.overlayReady.emit(packet)
        if mode == "rtsp":
            push_overlay.assert_called_once_with(packet, 2.0)
        else:
            push_overlay.assert_not_called()
        push_overlay.reset_mock()
        overlay_source = controller.overlay_source
        if overlay_source is not None:
            overlay_source.overlayReady.emit(packet)
            push_overlay.assert_called_once_with(packet, 2.0)
        source.sourceError.emit("failure")
        log_error.assert_called_once_with("Stream source error: failure")
        controller.start_playback()
        frame = _build_sync_result().frame.data
        source.frameReady.emit(frame)
        push_frame.assert_called_once()
        push_frame.reset_mock()
        push_overlay.reset_mock()
        controller.pause_playback()
        source.frameReady.emit(frame)
        if overlay_source is not None:
            overlay_source.overlayReady.emit(packet)
        push_frame.assert_not_called()
        push_overlay.assert_not_called()
        controller.resume_playback()
        reset_sync.assert_called_once()
        source.frameReady.emit(frame)
        push_frame.assert_called_once()
        if overlay_source is not None:
            overlay_source.overlayReady.emit(packet)
            push_overlay.assert_called_once_with(packet, 2.0)
        controller.cleanup()
        controller.cleanup()
        assert play.call_count == 2
        pause.assert_called_once()
        stop.assert_called_once()
        delete.assert_called_once()
        if mode == "mqtt":
            assert mqtt.plays == 2
            mqtt_pause.assert_called_once()
            mqtt_stop.assert_called_once()
            mqtt_delete.assert_called_once()


def test_same_named_streams_keep_independent_source_diagnostics(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    """Closing one identically named stream must not remove another stream's observations."""
    from ax_devil.modules.diagnostics.metrics_store import get_metrics_store, set_metrics_enabled
    from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store

    set_metrics_enabled(True)
    monkeypatch.setattr(
        "ax_devil.modules.video_viewer.stream_media_controller.RTSPSource", lambda _url, **_kwargs: _FrameSource()
    )
    controllers = []
    displays = []
    for _ in range(2):
        display = FrameDisplay()
        qtbot.addWidget(display)
        displays.append(display)
        controllers.append(
            StreamMediaController(
                display,
                _live_content("Same camera"),
                render_catalog_selection=render_catalog_manager.create_selection(),
            )
        )
    try:
        for controller in controllers:
            controller._on_sync_result(_build_sync_result())
        first_id, second_id = (controller._metrics_id for controller in controllers)
        assert first_id != second_id
        snapshots = {item.viewer_id: item for item in get_render_metrics_store().snapshot()}
        for display, controller in zip(displays, controllers):
            assert controller._metrics_id in snapshots[display.viewport._metrics_instance_id].source_ids
        controllers[0].cleanup()
        sources = get_metrics_store().snapshot()
        assert first_id not in sources
        assert sources[second_id].observations["Last synced frame"].value is not None
    finally:
        for controller in controllers:
            controller.cleanup()


class _LiveVideoSource(_FrameSource):
    """Video transport test double that records its lifecycle."""

    def __init__(self) -> None:
        super().__init__()
        self.plays = 0
        self.stopped = False

    def play(self) -> bool:
        self.plays += 1
        return True

    def stop(self) -> None:
        self.stopped = True


class _LiveOverlaySource(OverlaySource):
    """Separate overlay transport test double that records its lifecycle."""

    def __init__(self, **_kwargs: object) -> None:
        super().__init__()
        self.plays = 0
        self.stopped = False

    def play(self) -> bool:
        self.plays += 1
        return True

    def pause(self) -> None:
        return

    def stop(self) -> None:
        self.stopped = True

    def wait(self, timeout: int = 2000) -> bool:
        return True

    @property
    def handler_type(self) -> str:
        return "LIVE"

    def get_filter_config(self) -> None:
        return None


LiveTransports = tuple[list[_LiveVideoSource], list[_LiveOverlaySource]]


@pytest.fixture
def live_transports(monkeypatch: pytest.MonkeyPatch) -> LiveTransports:
    """Replace the RTSP and MQTT transports with recorded test doubles."""
    videos: list[_LiveVideoSource] = []
    overlays: list[_LiveOverlaySource] = []

    def open_video(_url: str, **_kwargs: object) -> _LiveVideoSource:
        videos.append(_LiveVideoSource())
        return videos[-1]

    def open_overlay(**kwargs: object) -> _LiveOverlaySource:
        overlays.append(_LiveOverlaySource(**kwargs))
        return overlays[-1]

    monkeypatch.setattr(stream_media_controller, "RTSPSource", open_video)
    monkeypatch.setattr("ax_devil.modules.data_sources.live.mqtt_overlay_source.MQTTOverlaySource", open_overlay)
    monkeypatch.setattr(stream_media_controller, "get_payload_decoder", lambda _handler: MagicMock())
    monkeypatch.setattr(stream_media_controller, "get_payload_filter_factory", lambda _handler: None)
    return videos, overlays


def _mqtt_live_content() -> LiveVideoContent:
    return LiveVideoContent(
        display_name="cam-1",
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass", stream_url="rtsp://x"),
        overlays=(
            OverlayContent(
                display_name="MQTT",
                source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
            ),
        ),
    )


def _states(status: LiveConnectionStatus) -> dict[LiveFeed, tuple[LiveConnectionState, str]]:
    return {feed.feed: (feed.state, feed.reason) for feed in status.feeds}


def test_live_controller_reports_video_and_overlay_connection_state(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    live_transports: LiveTransports,
) -> None:
    """Source signals and frames drive separate video and overlay states that the viewer receives."""
    display = FrameDisplay()
    qtbot.addWidget(display)
    published: list[LiveConnectionStatus] = []
    controller = StreamMediaController(
        display,
        _mqtt_live_content(),
        render_catalog_selection=render_catalog_manager.create_selection(),
        connection_changed=published.append,
    )
    (video,), (overlay,) = live_transports
    connecting = LiveConnectionState.CONNECTING
    try:
        controller.start_playback()
        assert _states(controller.connection_status) == {
            LiveFeed.VIDEO: (connecting, ""),
            LiveFeed.OVERLAY: (connecting, ""),
        }

        video.sourceConnected.emit()
        overlay.sourceReconnecting.emit("Connection refused")
        overlay.sourceReconnecting.emit("Connection refused")
        status = published[-1]
        assert status.headline.state is LiveConnectionState.LIVE
        assert status.feed(LiveFeed.OVERLAY).headline == "Reconnecting (2)"
        assert [problem.feed for problem in status.problems] == [LiveFeed.OVERLAY]
        assert not status.needs_retry

        overlay.sourceConnected.emit()
        video.sourceError.emit("RTSP Error: Unauthorized")
        assert _states(published[-1]) == {
            LiveFeed.VIDEO: (LiveConnectionState.FAILED, "RTSP Error: Unauthorized"),
            LiveFeed.OVERLAY: (LiveConnectionState.LIVE, ""),
        }
        assert published[-1].needs_retry

        published_count = len(published)
        video.frameReady.emit(_build_sync_result().frame.data)
        video.frameReady.emit(_build_sync_result().frame.data)
        assert controller.connection_status.headline.state is LiveConnectionState.LIVE
        assert len(published) == published_count + 1
    finally:
        controller.cleanup()


def test_live_controller_marks_silent_video_stalled_except_while_paused(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
    live_transports: LiveTransports,
) -> None:
    """A connected camera that stops sending frames is reported as stalled and retryable."""
    clock = MagicMock()
    clock.monotonic.return_value = 0.0
    monkeypatch.setattr(stream_media_controller, "time", clock)
    display = FrameDisplay()
    qtbot.addWidget(display)
    controller = StreamMediaController(
        display, _live_content(), render_catalog_selection=render_catalog_manager.create_selection()
    )
    (video,), _overlays = live_transports
    try:
        controller.start_playback()
        assert controller._stall_timer.isActive()
        silent_s = stream_media_controller.VIDEO_STALL_TIMEOUT_S + 1
        clock.monotonic.return_value = silent_s
        video.sourceConnected.emit()
        controller._stall_timer.timeout.emit()
        assert controller.connection_status.headline.state is LiveConnectionState.LIVE

        video.frameReady.emit(_build_sync_result().frame.data)
        controller.pause_playback()
        clock.monotonic.return_value = 2 * silent_s
        controller._stall_timer.timeout.emit()
        assert controller.connection_status.headline.state is LiveConnectionState.LIVE

        controller.resume_playback()
        assert controller._stall_timer.isActive()
        controller._stall_timer.timeout.emit()
        assert controller.connection_status.headline.state is LiveConnectionState.LIVE

        clock.monotonic.return_value = 3 * silent_s
        controller._stall_timer.timeout.emit()
        stalled = controller.connection_status.headline
        assert stalled.state is LiveConnectionState.STALLED
        assert stalled.reason == "No video received for 6 s."
        assert controller.connection_status.needs_retry

        video.frameReady.emit(_build_sync_result().frame.data)
        assert controller.connection_status.headline.state is LiveConnectionState.LIVE
    finally:
        controller.cleanup()


def test_live_controller_retry_reopens_every_transport(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    live_transports: LiveTransports,
) -> None:
    """Retry replaces failed transports, ignores the old ones and resumes playback."""
    display = FrameDisplay()
    qtbot.addWidget(display)
    published: list[LiveConnectionStatus] = []
    controller = StreamMediaController(
        display,
        _mqtt_live_content(),
        render_catalog_selection=render_catalog_manager.create_selection(),
        connection_changed=published.append,
    )
    videos, overlays = live_transports
    try:
        controller.start_playback()
        videos[0].sourceError.emit("RTSP Error: Unauthorized")
        controller.pause_playback()

        pending_error = threading.Thread(target=lambda: videos[0].sourceError.emit("old queued error"))
        pending_error.start()
        pending_error.join()

        controller.retry()

        assert [video.stopped for video in videos] == [True, False]
        assert [overlay.stopped for overlay in overlays] == [True, False]
        assert (videos[1].plays, overlays[1].plays) == (1, 1)
        assert controller.video_source is videos[1]
        assert controller.overlay_source is overlays[1]
        assert not controller.is_paused
        connecting = LiveConnectionStatus.connecting([LiveFeed.VIDEO, LiveFeed.OVERLAY])
        assert published[-1] == connecting

        videos[0].sourceError.emit("late error from the old transport")
        QCoreApplication.processEvents()
        assert controller.connection_status == connecting
        videos[1].sourceConnected.emit()
        assert controller.connection_status.headline.state is LiveConnectionState.LIVE
    finally:
        controller.cleanup()
    assert videos[1].stopped and overlays[1].stopped


def test_live_controller_retry_reports_reopen_failure(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
    live_transports: LiveTransports,
) -> None:
    """A transport that cannot be recreated leaves a failed, retryable video state."""
    display = FrameDisplay()
    qtbot.addWidget(display)
    controller = StreamMediaController(
        display, _live_content(), render_catalog_selection=render_catalog_manager.create_selection()
    )
    try:
        monkeypatch.setattr(
            stream_media_controller, "RTSPSource", MagicMock(side_effect=RuntimeError("RTSP is unavailable"))
        )

        controller.retry()

        status = controller.connection_status
        assert status.headline.state is LiveConnectionState.FAILED
        assert status.headline.reason == "Failed to reopen live stream: RTSP is unavailable"
        assert status.needs_retry
        assert live_transports[0][0].stopped
    finally:
        controller.cleanup()


def test_live_controller_publishes_status_on_gui_thread_for_worker_emits(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    live_transports: LiveTransports,
) -> None:
    """Transport callbacks run on their own threads; status changes still reach the viewer on the GUI thread."""
    display = FrameDisplay()
    qtbot.addWidget(display)
    published_on: list[tuple[LiveConnectionState, bool]] = []
    controller = StreamMediaController(
        display,
        _mqtt_live_content(),
        render_catalog_selection=render_catalog_manager.create_selection(),
        connection_changed=lambda status: published_on.append(
            (status.headline.state, threading.current_thread() is threading.main_thread())
        ),
    )
    (video,), (overlay,) = live_transports

    class _Emitter(QThread):
        def run(self) -> None:
            overlay.sourceReconnecting.emit("Connection refused")

    try:
        callback_thread = threading.Thread(target=video.sourceConnected.emit)
        callback_thread.start()
        callback_thread.join()
        worker = _Emitter()
        worker.start()
        assert worker.wait(2000)

        qtbot.waitUntil(lambda: len(published_on) == 2)
        assert published_on == [(LiveConnectionState.LIVE, True), (LiveConnectionState.LIVE, True)]
        assert controller.connection_status.feed(LiveFeed.OVERLAY).headline == "Reconnecting (1)"
    finally:
        controller.cleanup()


def test_visibility_refreshes_paused_live_overlay_without_another_frame(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    """A visibility change refreshes the retained display while the transport stays paused."""
    from ax_devil.modules.scene.model import BoundingBox, Classification, Entity, EntityId, Observation, Score
    from ax_devil.modules.scene.rendering import OverlayFeature
    from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings
    from ax_devil.modules.video_player.engine.render_context import RenderContext

    displayed: list[VideoFrameWithOverlays] = []
    display = FrameDisplay()
    qtbot.addWidget(display)
    monkeypatch.setattr(stream_media_controller, "RTSPSource", lambda _url, **_kwargs: _FrameSource())
    selection = render_catalog_manager.create_selection()
    controller = StreamMediaController(
        display, _live_content(), render_catalog_selection=selection, frame_displayed=displayed.append
    )
    result = _build_sync_result()
    assert result.overlay is not None
    entity = Entity(id=EntityId("object"))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.1, 0.3, 0.5),
            classification=[Classification("human", Score(0.8))],
            frame_number=7,
        )
    )
    result.overlay.data.content.add_entity(entity)
    try:
        controller._on_sync_result(result)
        controller.pause_playback()
        overlay = displayed[0].overlays
        assert overlay is not None
        context = RenderContext.create(640, 480)
        initial = overlay.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
        with patch.object(display, "refresh_overlays", wraps=display.refresh_overlays) as refresh:
            selection.set_feature_enabled(OverlayFeature.CONFIDENCE, False)
            refresh.assert_called_once()
        hidden = overlay.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
        assert len(hidden) < len(initial)
        assert controller.is_paused and len(displayed) == 1
        assert overlay.interaction_provider is not None
        assert overlay.interaction_provider.get_hit_by_id("object") is not None
    finally:
        controller.cleanup()
