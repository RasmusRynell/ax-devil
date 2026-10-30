"""Fixtures for live stream controller tests: recorded transports, a test clock and a controller factory."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager, SceneRenderCatalogSelection
from ax_devil.modules.synchronization import engine as sync_engine
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer import live_transports as live_transports_module
from ax_devil.modules.video_viewer import stream_media_controller
from ax_devil.modules.video_viewer.live_connection import LiveConnectionStatus
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.stream_media_controller import StreamMediaController
from ax_devil.modules.workspace.core import (
    LiveVideoContent,
)
from tests.helpers.live_stream import (
    FakeClock,
    LiveTransports,
    MakeController,
    Transport,
)


@pytest.fixture
def live_transports(monkeypatch: pytest.MonkeyPatch) -> LiveTransports:
    """Replace the RTSP, MQTT and WebSocket transports with recorded test doubles."""
    videos: list[Transport] = []
    overlays: list[Transport] = []

    def open_video(url: str, **options: object) -> Transport:
        videos.append(Transport(url, options))
        return videos[-1]

    def open_overlay(**options: object) -> Transport:
        overlays.append(Transport("", options))
        return overlays[-1]

    monkeypatch.setattr(stream_media_controller, "RTSPSource", open_video)
    monkeypatch.setattr("ax_devil.modules.data_sources.live.mqtt_overlay_source.MQTTOverlaySource", open_overlay)
    monkeypatch.setattr(
        "ax_devil.modules.data_sources.live.websocket_overlay_source.WebSocketOverlaySource", open_overlay
    )
    monkeypatch.setattr(live_transports_module, "get_payload_decoder", lambda handler: f"decoder:{handler}")
    monkeypatch.setattr(live_transports_module, "get_payload_filter_factory", lambda handler: f"filters:{handler}")
    return videos, overlays


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Drive the stall watchdog and the live synchronization delay from one test clock."""
    fake = FakeClock()
    monkeypatch.setattr(stream_media_controller, "time", fake)
    monkeypatch.setattr(sync_engine, "time", fake)
    return fake


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
                overlay_persistence=OverlayPersistenceSettings.default_enabled(),
                frame_displayed=frame_displayed,
                connection_changed=connection_changed,
            )
        )
        return controllers[-1]

    yield make
    for controller in controllers:
        controller.cleanup()
