"""Tests for the connection state, retry and paused refresh of StreamMediaController."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QThread
from pytestqt.qtbot import QtBot

from ax_devil.modules.filtering import build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import Delete, EntityId, TimeSlice
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager, SceneRenderCatalogSelection
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_viewer import stream_media_controller
from ax_devil.modules.video_viewer.live_connection import LiveConnectionState, LiveConnectionStatus, LiveFeed
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from tests.helpers.live_stream import (
    MQTT_OVERLAY,
    RTSP_OVERLAY,
    FakeClock,
    LiveTransports,
    MakeController,
    make_frame,
    make_live_content,
    make_sync_result,
)


def test_live_controller_reports_video_and_overlay_connection_state(
    make_controller: MakeController, live_transports: LiveTransports
) -> None:
    """Source signals and frames drive separate video and overlay states that the viewer receives."""
    published: list[LiveConnectionStatus] = []
    controller = make_controller(make_live_content(MQTT_OVERLAY), connection_changed=published.append)
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
    video.frameReady.emit(make_frame(1))
    video.frameReady.emit(make_frame(2))
    assert controller.connection_status.headline.state is LiveConnectionState.LIVE
    assert len(published) == published_count + 1


def test_live_controller_marks_silent_video_stalled_except_while_paused(
    make_controller: MakeController, live_transports: LiveTransports, clock: FakeClock
) -> None:
    """A connected camera that stops sending frames is reported as stalled and retryable."""
    controller = make_controller(make_live_content())
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

    video.frameReady.emit(make_frame(1))
    assert controller.connection_status.headline.state is LiveConnectionState.LIVE


def test_live_controller_retry_reopens_every_transport(
    make_controller: MakeController,
    live_transports: LiveTransports,
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
) -> None:
    """Retry replaces failed transports and resumes playback without discarding recorded events."""
    published: list[LiveConnectionStatus] = []
    controller = make_controller(make_live_content(MQTT_OVERLAY), connection_changed=published.append)
    panel = MediaToolsPanel(render_catalog_selection, filter_model=SessionFilter(build_default_filter_config()))
    qtbot.addWidget(panel, before_close_func=lambda panel: panel.cleanup())
    controller.attach_scene_inspector(panel)
    videos, overlays = live_transports
    controller.start_playback()
    result = make_sync_result()
    assert result.overlay is not None and controller.synchronizer is not None
    result.overlay.data.content.add_event(Delete(timestamp=TimeSlice(start=0, end=1), entity_id=EntityId("kept")))
    controller.synchronizer.syncReady.emit(result)
    assert panel.events_page.count == 1
    videos[0].sourceError.emit("RTSP Error: Unauthorized")
    controller.pause_playback()

    pending_error = threading.Thread(target=lambda: videos[0].sourceError.emit("old queued error"))
    pending_error.start()
    pending_error.join()

    controller.retry()

    assert panel.events_page.count == 1
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
    controller = make_controller(make_live_content())
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
        make_live_content(MQTT_OVERLAY),
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
    controller = make_controller(make_live_content(RTSP_OVERLAY), selection=selection, frame_displayed=displayed.append)
    result = make_sync_result(7)
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
