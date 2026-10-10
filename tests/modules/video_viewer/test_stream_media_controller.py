"""Tests for StreamMediaController with spec-backed LiveVideoContent."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import TypeAlias
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QThread
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.base import FrameSource, OverlaySource
from ax_devil.modules.data_sources.live.rtsp_source import RTSPOverlayDecoder
from ax_devil.modules.filtering import FilterConfig, build_default_filter_config
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager, SceneRenderCatalogSelection
from ax_devil.modules.synchronization import SyncResult, TimestampedData
from ax_devil.modules.synchronization import engine as sync_engine
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer import stream_media_controller
from ax_devil.modules.video_viewer.live_connection import LiveConnectionState, LiveConnectionStatus, LiveFeed
from ax_devil.modules.video_viewer.stream_media_controller import StreamMediaController
from ax_devil.modules.workspace import (
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
    OverlayContent,
)
from tests.helpers.scene_inspector import RecordingSceneInspector

OverlaySpec: TypeAlias = LiveRTSPOverlaySourceSpec | LiveMQTTOverlaySourceSpec | LiveWebSocketOverlaySourceSpec
RTSP_OVERLAY = LiveRTSPOverlaySourceSpec(handler_type="LIVE")
MQTT_OVERLAY = LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local")


class _Transport(FrameSource, OverlaySource):
    """Live transport test double that records how it was opened and its lifecycle."""

    def __init__(self, url: str, options: dict[str, object]) -> None:
        super().__init__()
        self.url = url
        self.options = options
        self.filter_config = build_default_filter_config()
        self.plays = 0
        self.pauses = 0
        self.stops = 0
        self.deletes = 0

    def play(self) -> bool:
        self.plays += 1
        return True

    def pause(self) -> None:
        self.pauses += 1

    def stop(self) -> None:
        self.stops += 1

    def wait(self, timeout: int = 2000) -> bool:
        return True

    def deleteLater(self) -> None:
        self.deletes += 1

    @property
    def handler_type(self) -> str:
        return "LIVE"

    def get_filter_config(self) -> FilterConfig:
        return self.filter_config


LiveTransports = tuple[list[_Transport], list[_Transport]]


@pytest.fixture
def live_transports(monkeypatch: pytest.MonkeyPatch) -> LiveTransports:
    """Replace the RTSP, MQTT and WebSocket transports with recorded test doubles."""
    videos: list[_Transport] = []
    overlays: list[_Transport] = []

    def open_video(url: str, **options: object) -> _Transport:
        videos.append(_Transport(url, options))
        return videos[-1]

    def open_overlay(**options: object) -> _Transport:
        overlays.append(_Transport("", options))
        return overlays[-1]

    monkeypatch.setattr(stream_media_controller, "RTSPSource", open_video)
    monkeypatch.setattr("ax_devil.modules.data_sources.MQTTOverlaySource", open_overlay)
    monkeypatch.setattr("ax_devil.modules.data_sources.WebSocketOverlaySource", open_overlay)
    monkeypatch.setattr(stream_media_controller, "get_payload_decoder", lambda handler: f"decoder:{handler}")
    monkeypatch.setattr(stream_media_controller, "get_payload_filter_factory", lambda handler: f"filters:{handler}")
    return videos, overlays


class _Clock:
    """Monotonic clock that only moves when a test advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Drive the stall watchdog and the live synchronization delay from one test clock."""
    fake = _Clock()
    monkeypatch.setattr(stream_media_controller, "time", fake)
    monkeypatch.setattr(sync_engine, "time", fake)
    return fake


MakeController = Callable[..., StreamMediaController]


@pytest.fixture
def make_controller(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> Iterator[MakeController]:
    """Build controllers on their own frame display and clean every one up after the test."""
    controllers: list[StreamMediaController] = []

    def make(
        content: LiveVideoContent,
        *,
        selection: SceneRenderCatalogSelection | None = None,
        frame_displayed: Callable[[VideoFrameWithOverlays], None] | None = None,
        connection_changed: Callable[[LiveConnectionStatus], None] | None = None,
    ) -> StreamMediaController:
        display = FrameDisplay()
        qtbot.addWidget(display)
        controllers.append(
            StreamMediaController(
                display,
                content,
                render_catalog_selection=selection or render_catalog_manager.create_selection(),
                frame_displayed=frame_displayed,
                connection_changed=connection_changed,
            )
        )
        return controllers[-1]

    yield make
    for controller in controllers:
        controller.cleanup()


def _live_content(overlay: OverlaySpec | None = None, name: str = "cam-1") -> LiveVideoContent:
    return LiveVideoContent(
        display_name=name,
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass"),
        overlays=(OverlayContent(display_name="overlay", source_spec=overlay),) if overlay else (),
    )


def _frame_id(seconds: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=seconds, timestamp_monotime_us=seconds * 1_000_000.0)


def _frame(seconds: int) -> FrameData:
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0)
    return FrameData(frame_id=_frame_id(seconds), content=image, source_id="video")


def _overlay(seconds: int) -> OverlayData:
    return OverlayData(frame_id=_frame_id(seconds), content=Scene(time_slice=TimeSlice(start=0, end=1)))


def _sync_result(seconds: int = 7) -> SyncResult[FrameData, OverlayData]:
    return SyncResult(
        frame=TimestampedData(data=_frame(seconds), capture_time=seconds, arrival_time=seconds),
        overlay=TimestampedData(data=_overlay(seconds), capture_time=seconds, arrival_time=seconds),
    )


def _displayed_ids(displayed: list[VideoFrameWithOverlays]) -> list[int | None]:
    return [item.frame.frame_id for item in displayed]


def test_embedded_rtsp_overlay_requests_analytics_on_the_video_stream(
    make_controller: MakeController, live_transports: LiveTransports
) -> None:
    controller = make_controller(_live_content(RTSP_OVERLAY))

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
    controller = make_controller(_live_content(overlay_spec))

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
    clock: _Clock,
    overlay_spec: OverlaySpec | None,
) -> None:
    """Frames and overlays from the configured transports are synchronized before display."""
    displayed: list[VideoFrameWithOverlays] = []
    controller = make_controller(_live_content(overlay_spec), frame_displayed=displayed.append)
    videos, overlays = live_transports
    controller.start_playback()

    (overlays or videos)[0].overlayReady.emit(_overlay(1))
    videos[0].frameReady.emit(_frame(1))
    clock.now = 10.0
    videos[0].frameReady.emit(_frame(2))

    assert _displayed_ids(displayed) == [1]
    overlay = displayed[0].overlays
    if overlay_spec is None:
        assert overlay is None
    else:
        assert overlay is not None and overlay.overlay_id == 1


def test_paused_stream_ignores_frames_and_resume_discards_frames_buffered_before_the_pause(
    make_controller: MakeController, live_transports: LiveTransports, clock: _Clock
) -> None:
    displayed: list[VideoFrameWithOverlays] = []
    controller = make_controller(_live_content(), frame_displayed=displayed.append)
    (video,), _overlays = live_transports
    controller.start_playback()
    video.frameReady.emit(_frame(1))

    controller.pause_playback()
    clock.now = 10.0
    video.frameReady.emit(_frame(2))

    assert controller.is_paused and video.pauses == 1
    assert displayed == []

    controller.resume_playback()
    video.frameReady.emit(_frame(10))
    clock.now = 20.0
    video.frameReady.emit(_frame(20))

    assert _displayed_ids(displayed) == [10]


@pytest.mark.parametrize("overlay_spec", [RTSP_OVERLAY, MQTT_OVERLAY], ids=["rtsp", "mqtt"])
def test_cleanup_releases_every_transport_exactly_once(
    make_controller: MakeController, live_transports: LiveTransports, overlay_spec: OverlaySpec
) -> None:
    controller = make_controller(_live_content(overlay_spec))
    controller.start_playback()

    controller.cleanup()
    controller.cleanup()

    videos, overlays = live_transports
    assert [(transport.stops, transport.deletes) for transport in videos + overlays] == [(1, 1)] * len(
        videos + overlays
    )


def test_live_controller_displays_frame_before_scheduling_scene_inspection(
    qtbot: QtBot, make_controller: MakeController, live_transports: LiveTransports
) -> None:
    inspector = RecordingSceneInspector()
    displayed: list[VideoFrameWithOverlays] = []

    def record_displayed(frame: VideoFrameWithOverlays) -> None:
        assert inspector.updates == []
        displayed.append(frame)

    controller = make_controller(_live_content(RTSP_OVERLAY), frame_displayed=record_displayed)
    controller.attach_scene_inspector(inspector)
    assert controller.synchronizer is not None

    controller.synchronizer.syncReady.emit(_sync_result(7))

    assert inspector.cleared is True
    assert _displayed_ids(displayed) == [7]
    assert displayed[0].overlays is not None and displayed[0].overlays.overlay_id == 7
    assert inspector.updates == []
    qtbot.waitUntil(lambda: len(inspector.updates) == 1)
    assert [frame_id for _scene, frame_id, _metadata in inspector.updates] == [_frame_id(7)]


def test_same_named_streams_keep_independent_source_diagnostics(
    make_controller: MakeController, live_transports: LiveTransports
) -> None:
    """Closing one identically named stream must not remove another stream's observations."""
    from ax_devil.modules.diagnostics.metrics_store import get_metrics_store, set_metrics_enabled
    from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store

    set_metrics_enabled(True)
    controllers = [make_controller(_live_content(name="Same camera")) for _ in range(2)]

    def synced_streams() -> set[str]:
        return {
            source_id
            for source_id, source in get_metrics_store().snapshot().items()
            if source.label.endswith("Same camera") and "Last synced frame" in source.observations
        }

    for controller in controllers:
        assert controller.synchronizer is not None
        controller.synchronizer.syncReady.emit(_sync_result())
    stream_ids = synced_streams()
    assert len(stream_ids) == 2
    viewer_links = [set(viewer.source_ids) & stream_ids for viewer in get_render_metrics_store().snapshot()]
    assert sorted(len(link) for link in viewer_links if link) == [1, 1]

    controllers[0].cleanup()

    remaining = synced_streams()
    assert len(remaining) == 1 and remaining < stream_ids


def test_live_controller_reports_video_and_overlay_connection_state(
    make_controller: MakeController, live_transports: LiveTransports
) -> None:
    """Source signals and frames drive separate video and overlay states that the viewer receives."""
    published: list[LiveConnectionStatus] = []
    controller = make_controller(_live_content(MQTT_OVERLAY), connection_changed=published.append)
    (video,), (overlay,) = live_transports
    connecting = LiveConnectionState.CONNECTING

    controller.start_playback()
    assert {feed.feed: feed.state for feed in controller.connection_status.feeds} == {
        LiveFeed.VIDEO: connecting,
        LiveFeed.OVERLAY: connecting,
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
    assert {feed.feed: (feed.state, feed.reason) for feed in published[-1].feeds} == {
        LiveFeed.VIDEO: (LiveConnectionState.FAILED, "RTSP Error: Unauthorized"),
        LiveFeed.OVERLAY: (LiveConnectionState.LIVE, ""),
    }
    assert published[-1].needs_retry

    published_count = len(published)
    video.frameReady.emit(_frame(1))
    video.frameReady.emit(_frame(2))
    assert controller.connection_status.headline.state is LiveConnectionState.LIVE
    assert len(published) == published_count + 1


def test_live_controller_marks_silent_video_stalled_except_while_paused(
    make_controller: MakeController, live_transports: LiveTransports, clock: _Clock
) -> None:
    """A connected camera that stops sending frames is reported as stalled and retryable."""
    controller = make_controller(_live_content())
    (video,), _overlays = live_transports
    # The watchdog timer is private; emitting its timeout checks for a stall without waiting a real second.
    watchdog = controller._stall_timer

    def headline_after_check() -> LiveConnectionState:
        watchdog.timeout.emit()
        return controller.connection_status.headline.state

    controller.start_playback()
    assert watchdog.isActive()
    clock.now = 30.0
    video.sourceConnected.emit()
    assert headline_after_check() is LiveConnectionState.LIVE

    controller.pause_playback()
    clock.now = 90.0
    assert headline_after_check() is LiveConnectionState.LIVE

    controller.resume_playback()
    assert headline_after_check() is LiveConnectionState.LIVE

    clock.now = 150.0
    assert headline_after_check() is LiveConnectionState.STALLED
    assert controller.connection_status.needs_retry

    video.frameReady.emit(_frame(1))
    assert controller.connection_status.headline.state is LiveConnectionState.LIVE


def test_live_controller_retry_reopens_every_transport(
    make_controller: MakeController, live_transports: LiveTransports
) -> None:
    """Retry replaces failed transports, ignores the old ones and resumes playback."""
    published: list[LiveConnectionStatus] = []
    controller = make_controller(_live_content(MQTT_OVERLAY), connection_changed=published.append)
    videos, overlays = live_transports
    controller.start_playback()
    videos[0].sourceError.emit("RTSP Error: Unauthorized")
    controller.pause_playback()

    pending_error = threading.Thread(target=lambda: videos[0].sourceError.emit("old queued error"))
    pending_error.start()
    pending_error.join()

    controller.retry()

    assert [transport.stops for transport in videos + overlays] == [1, 0, 1, 0]
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

    controller.cleanup()
    assert videos[1].stops == overlays[1].stops == 1


def test_live_controller_retry_reports_reopen_failure(
    monkeypatch: pytest.MonkeyPatch, make_controller: MakeController, live_transports: LiveTransports
) -> None:
    """A transport that cannot be recreated leaves a failed, retryable video state."""
    controller = make_controller(_live_content())
    monkeypatch.setattr(stream_media_controller, "RTSPSource", MagicMock(side_effect=RuntimeError("RTSP is down")))

    controller.retry()

    status = controller.connection_status
    assert status.headline.state is LiveConnectionState.FAILED
    assert "RTSP is down" in status.headline.reason
    assert status.needs_retry
    assert live_transports[0][0].stops == 1


def test_live_controller_publishes_status_on_gui_thread_for_worker_emits(
    qtbot: QtBot, make_controller: MakeController, live_transports: LiveTransports
) -> None:
    """Transport callbacks run on their own threads; status changes still reach the viewer on the GUI thread."""
    published_on: list[tuple[LiveConnectionState, bool]] = []
    controller = make_controller(
        _live_content(MQTT_OVERLAY),
        connection_changed=lambda status: published_on.append(
            (status.headline.state, threading.current_thread() is threading.main_thread())
        ),
    )
    (video,), (overlay,) = live_transports

    class _Emitter(QThread):
        def run(self) -> None:
            overlay.sourceReconnecting.emit("Connection refused")

    callback_thread = threading.Thread(target=video.sourceConnected.emit)
    callback_thread.start()
    callback_thread.join()
    worker = _Emitter()
    worker.start()
    assert worker.wait(2000)

    qtbot.waitUntil(lambda: len(published_on) == 2)
    assert published_on == [(LiveConnectionState.LIVE, True), (LiveConnectionState.LIVE, True)]
    assert controller.connection_status.feed(LiveFeed.OVERLAY).headline == "Reconnecting (1)"


def test_visibility_refreshes_paused_live_overlay_without_another_frame(
    make_controller: MakeController,
    live_transports: LiveTransports,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    """A visibility change refreshes the retained display while the transport stays paused."""
    from ax_devil.modules.scene.model import BoundingBox, Classification, Entity, EntityId, Observation, Score
    from ax_devil.modules.scene.rendering import OverlayFeature
    from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings
    from ax_devil.modules.video_player.engine.render_context import RenderContext

    displayed: list[VideoFrameWithOverlays] = []
    selection = render_catalog_manager.create_selection()
    controller = make_controller(_live_content(RTSP_OVERLAY), selection=selection, frame_displayed=displayed.append)
    result = _sync_result(7)
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
    assert controller.synchronizer is not None and controller.frame_display is not None
    controller.synchronizer.syncReady.emit(result)
    controller.pause_playback()
    overlay = displayed[0].overlays
    assert overlay is not None
    context = RenderContext.create(640, 480)
    initial = overlay.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context)))

    with patch.object(
        controller.frame_display, "refresh_overlays", wraps=controller.frame_display.refresh_overlays
    ) as refresh:
        selection.set_feature_enabled(OverlayFeature.CONFIDENCE, False)

    refresh.assert_called_once()
    hidden = overlay.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert len(hidden) < len(initial)
    assert controller.is_paused and len(displayed) == 1
