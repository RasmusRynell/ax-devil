"""Tests for DataHub overlay decoding and Qt source lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Event
from typing import Any, cast

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import OverlayData
from ax_devil.modules.data_sources.live.datahub_client import (
    DataHubTopicSample,
    DataHubWebSocketClient,
    DataHubWebSocketError,
)
from ax_devil.modules.data_sources.live.websocket_overlay_source import WebSocketOverlaySource
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Entity, EntityId, Scene, TimeSlice


class _Decoder(PayloadToSceneDecoder):
    """Decode ``{"seconds": float, "id": str}`` into a one-entity scene at that device time."""

    def decode(self, payload: Any) -> Scene:
        start = datetime.fromtimestamp(payload["seconds"], tz=timezone.utc)
        return Scene(
            time_slice=TimeSlice(start=start, end=start),
            entities={EntityId(payload["id"]): Entity(id=EntityId(payload["id"]))},
        )


class _FakeClient:
    """Offline DataHub client: replays samples or errors, then idles until cancelled."""

    def __init__(self, items: list[DataHubTopicSample | Exception], *, block_connect: bool = False) -> None:
        self.items = items
        self.block_connect = block_connect
        self.waiting = Event()
        self.closed = False

    def __call__(self, **_kwargs: object) -> _FakeClient:
        return self

    async def connect(self) -> None:
        if self.block_connect:
            self.waiting.set()
            await asyncio.Future[None]()

    async def receive_sample(self) -> DataHubTopicSample:
        if not self.items:
            self.waiting.set()
            await asyncio.Future[None]()
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def _source(client: _FakeClient) -> WebSocketOverlaySource:
    return WebSocketOverlaySource(
        topic="topic",
        channel_id=4,
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        decoder=_Decoder(),
        handler_type="test",
        client_factory=cast(Callable[..., DataHubWebSocketClient], client),
    )


def _sample(seconds: float, entity_id: str) -> DataHubTopicSample:
    return DataHubTopicSample(
        topic="topic",
        channel_id=4,
        data={"seconds": seconds, "id": entity_id},
        timestamp="2026-03-17T09:39:52.569Z",
        is_historical=False,
    )


def test_samples_become_overlays_keyed_by_scene_time(qtbot: QtBot) -> None:
    """Overlays carry the decoded scene's device time, not the DataHub envelope time, in arrival order."""
    client = _FakeClient([_sample(12.5, "a"), _sample(13.0, "b")])
    source = _source(client)
    overlays: list[OverlayData] = []
    source.overlayReady.connect(overlays.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: len(overlays) == 2)
    finally:
        source.stop()
        source.deleteLater()

    assert [overlay.frame_id.sequence_id for overlay in overlays] == [1, 2]
    assert [overlay.frame_id.timestamp_monotime_us for overlay in overlays] == [12_500_000, 13_000_000]
    assert set(overlays[0].content.entities) == {EntityId("a")}
    assert overlays[0].metadata == {
        "websocket_topic": "topic",
        "websocket_channel_id": 4,
        "websocket_timestamp": "2026-03-17T09:39:52.569Z",
        "websocket_is_historical": False,
    }


def test_source_reports_connection_then_transport_failure(qtbot: QtBot) -> None:
    """The live viewer sees the DataHub connection open and then the reason it dropped."""
    client = _FakeClient([DataHubWebSocketError("DataHub WebSocket transport error.")])
    source = _source(client)
    events: list[str] = []
    source.sourceConnected.connect(lambda: events.append("connected"))
    source.sourceError.connect(events.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: len(events) == 2)
        assert source.wait()
    finally:
        source.stop()
        source.deleteLater()

    assert events == ["connected", "DataHub WebSocket transport error."]
    assert client.closed


@pytest.mark.parametrize("block_connect", [True, False], ids=["connect", "receive_sample"])
def test_source_stop_cancels_pending_io_and_closes_client(qtbot: QtBot, block_connect: bool) -> None:
    """Stopping interrupts connection and idle receive waits, and returns only after the client is closed."""
    client = _FakeClient([], block_connect=block_connect)
    source = _source(client)
    errors: list[str] = []
    source.sourceError.connect(errors.append)
    try:
        assert source.play()
        qtbot.waitUntil(client.waiting.is_set)
        source.stop()
        assert client.closed
        assert source.wait(0)
        assert not errors
    finally:
        source.stop()
        source.deleteLater()
