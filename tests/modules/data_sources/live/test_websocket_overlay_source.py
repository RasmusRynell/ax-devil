"""Tests for DataHub overlay decoding and Qt source lifecycle."""

from __future__ import annotations

import asyncio
from threading import Event
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.data_sources.live.datahub_client import DataHubTopicSample, DataHubWebSocketError
from ax_devil.modules.data_sources.live.websocket_overlay_source import WebSocketOverlaySource, WebSocketWorker


class _ClosedEventLoop:
    def call_soon_threadsafe(self, callback: object) -> None:
        del callback
        raise RuntimeError("loop closed")


class _PendingTask:
    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        return None


def test_websocket_worker_decodes_scene_timestamp_and_bounded_metadata() -> None:
    decoder = MagicMock()
    scene = MagicMock()
    scene.time_slice.is_instant = True
    scene.time_slice.start.timestamp.return_value = 12.5
    decoder.decode.return_value = scene
    signal = MagicMock()
    worker = WebSocketWorker(
        topic="topic",
        channel_id=4,
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        decoder=decoder,
    )
    worker.set_overlay_ready_signal(signal)

    worker._decode_sample(
        DataHubTopicSample(
            topic="topic",
            channel_id=4,
            data={"payload": True},
            timestamp="2026-03-17T09:39:52.569Z",
            is_historical=False,
        )
    )

    overlay_data = signal.emit.call_args.args[0]
    assert overlay_data.frame_id.sequence_id == 1
    assert overlay_data.frame_id.timestamp_monotime_us == 12_500_000.0
    assert overlay_data.metadata == {
        "websocket_topic": "topic",
        "websocket_channel_id": 4,
        "websocket_timestamp": "2026-03-17T09:39:52.569Z",
        "websocket_is_historical": False,
    }
    decoder.decode.assert_called_once_with({"payload": True})


def test_websocket_worker_stop_tolerates_closed_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = WebSocketWorker(
        topic="topic",
        channel_id=1,
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        decoder=MagicMock(),
    )
    monkeypatch.setattr(worker, "_loop", _ClosedEventLoop())
    monkeypatch.setattr(worker, "_task", _PendingTask())

    worker.request_stop()


def test_websocket_worker_handles_cancellation_during_connect() -> None:
    client = MagicMock()
    client.connect = AsyncMock(side_effect=asyncio.CancelledError)
    client.close = AsyncMock()
    worker = WebSocketWorker(
        topic="topic",
        channel_id=1,
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        decoder=MagicMock(),
        client_factory=MagicMock(return_value=client),
    )
    worker._running = True

    asyncio.run(worker._run_async())

    client.close.assert_awaited_once()


def test_websocket_worker_reports_connection_then_transport_failure(qtbot: QtBot) -> None:
    """The live viewer sees the DataHub connection open and then the reason it dropped."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.receive_sample = AsyncMock(side_effect=DataHubWebSocketError("DataHub WebSocket transport error."))
    client.close = AsyncMock()
    worker = WebSocketWorker(
        topic="topic",
        channel_id=1,
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        decoder=MagicMock(),
        client_factory=MagicMock(return_value=client),
    )
    events: list[str] = []
    worker.sourceConnected.connect(lambda: events.append("connected"))
    worker.sourceError.connect(events.append)
    worker._running = True

    asyncio.run(worker._run_async())

    assert events == ["connected", "DataHub WebSocket transport error."]
    client.close.assert_awaited_once()


@pytest.mark.parametrize("stage", ["connect", "receive_sample"])
def test_source_stop_cancels_pending_io_and_closes_client(qtbot: QtBot, stage: str) -> None:
    """Stopping the real Qt worker interrupts both connection and idle receive waits."""
    entered = Event()
    client = MagicMock()

    async def pending() -> None:
        entered.set()
        await asyncio.Future[None]()

    client.connect = AsyncMock()
    client.receive_sample = AsyncMock()
    client.close = AsyncMock()
    setattr(client, stage, pending)
    source = WebSocketOverlaySource(
        topic="topic",
        channel_id=1,
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        decoder=MagicMock(),
        handler_type="test",
        client_factory=MagicMock(return_value=client),
    )
    errors: list[str] = []
    source.sourceError.connect(errors.append)
    try:
        assert source.play()
        qtbot.waitUntil(entered.is_set)
        source.stop()
        assert source.wait(100)
        assert not errors
        client.close.assert_awaited_once()
    finally:
        source.stop()
        source.deleteLater()


def test_websocket_overlay_stop_waits_for_worker_before_final_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = MagicMock()
    events: list[str] = []
    worker.request_stop.side_effect = lambda: events.append("request-stop")
    worker.stop.side_effect = lambda: events.append("stop")

    def wait(timeout: int) -> bool:
        events.append(f"wait-{timeout}")
        return True

    worker.wait.side_effect = wait
    monkeypatch.setattr(
        "ax_devil.modules.data_sources.live.websocket_overlay_source.WebSocketWorker",
        MagicMock(return_value=worker),
    )
    source = WebSocketOverlaySource(
        topic="topic",
        channel_id=1,
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        decoder=MagicMock(),
        handler_type="ADF_V1_FRAME",
    )

    source.stop()

    assert events == ["request-stop", "wait-2000"]
    worker.wait.assert_called_once_with(2000)
    worker.set_overlay_ready_signal.assert_any_call(None)
