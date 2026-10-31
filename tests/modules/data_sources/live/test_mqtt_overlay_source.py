"""Tests for MQTT overlay connection errors and lifecycle."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import pytest
from ax_devil_device_api.utils.errors import AuthenticationError, ConfigurationError
from ax_devil_mqtt import MqttMessage
from ax_devil_mqtt.core.manager import logger as dependency_logger
from PySide6.QtCore import QTimer
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import OverlayData
from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.modules.data_sources.live import mqtt_discovery, mqtt_overlay_source
from ax_devil.modules.data_sources.live.mqtt_overlay_source import MQTTOverlaySource
from ax_devil.modules.scene.model import EntityId, Scene, Score
from ax_devil.plugins.decoders.adf_v1.frame import ADFFrameV1Decoder


class _AnalyticsMqttClient:
    def get_data_sources(self) -> list[dict[str, str]]:
        return [
            {"key": "com.axis.scene.frame.v1#1"},
            {"key": "com.axis.scene.object_track.v1#1"},
            {"key": "com.axis.scene.frame.v1#2"},
        ]


class _DeviceClient:
    analytics_mqtt = _AnalyticsMqttClient()

    def __init__(self, _config: object) -> None:
        pass

    def __enter__(self) -> _DeviceClient:
        return self

    def __exit__(self, *_args: object) -> None:
        pass


class _Broker:
    """Offline AxisAnalyticsMqttClient factory that records the client lifecycle."""

    def __init__(self, start_results: list[Exception | None] | None = None) -> None:
        self.start_results = start_results or []
        self.events: list[str] = []
        self.message_callback: Callable[[MqttMessage], None] | None = None
        self.release_start = threading.Event()
        self.release_start.set()
        self.start_entered = threading.Event()

    def __call__(self, *, message_callback: Callable[[MqttMessage], None], **_kwargs: object) -> _Broker:
        self.message_callback = message_callback
        return self

    def start(self) -> None:
        self.events.append("start")
        self.start_entered.set()
        self.release_start.wait(5)
        self.events.append("started")
        if self.start_results:
            result = self.start_results.pop(0)
            if result is not None:
                dependency_logger.error(f"Setup failed: {result}", exc_info=(type(result), result, None))
                raise result

    def stop(self) -> None:
        self.events.append("stop")


class _RecordingDecoder(ADFFrameV1Decoder):
    """Real ADF decoder that records each payload the worker hands it."""

    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[Any] = []

    def decode(self, payload: Any) -> Scene | None:
        self.payloads.append(payload)
        return super().decode(payload)


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> _Broker:
    """Replace the device API and MQTT broker connection with offline fakes."""
    monkeypatch.setattr(mqtt_discovery, "Client", _DeviceClient)
    fake = _Broker()
    monkeypatch.setattr(mqtt_overlay_source, "AxisAnalyticsMqttClient", fake)
    return fake


def _source(
    data_source_key: str = "com.axis.scene.frame.v1#1", decoder: ADFFrameV1Decoder | None = None
) -> MQTTOverlaySource:
    return MQTTOverlaySource(
        broker_host="broker.local",
        broker_port=1883,
        broker_username="fake-broker-user",
        broker_password="fake-secret /+?",
        device=DeviceConnection("camera.local", DeviceProtocol.HTTPS, 0, "root", "pass"),
        analytics_data_source_key=data_source_key,
        decoder=decoder or ADFFrameV1Decoder(),
        handler_type="ADF_V1_FRAME",
        source_id="test-camera",
    )


def test_mqtt_delivery_recovers_after_malformed_payload(qtbot: QtBot, broker: _Broker) -> None:
    """Real decoding and Qt emission preserve camera time after a bad transport packet."""
    decoder = _RecordingDecoder()
    source = _source(decoder=decoder)
    overlays: list[OverlayData] = []
    connected: list[bool] = []
    source.overlayReady.connect(overlays.append)
    source.sourceConnected.connect(lambda: connected.append(True))
    message = MqttMessage(
        topic="analytics",
        payload=json.dumps(
            {
                "frame": {
                    "timestamp": "1970-01-01T00:00:12.500Z",
                    "detections": [
                        {
                            "object_track_id": "42",
                            "bounding_box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.6},
                            "class": {"type": "human", "score": 0.9},
                        }
                    ],
                }
            }
        ),
    )
    try:
        assert source.play()
        qtbot.waitUntil(lambda: connected == [True])
        assert broker.message_callback is not None
        broker.message_callback(MqttMessage(topic="analytics", payload="{invalid"))
        qtbot.waitUntil(lambda: "{invalid" in decoder.payloads)
        broker.message_callback(message)
        qtbot.waitUntil(lambda: len(overlays) >= 1)
    finally:
        source.stop()
        assert source.wait()
        source.deleteLater()

    assert len(overlays) == 1
    overlay = overlays[0]
    assert overlay.source_id == "test-camera"
    assert overlay.frame_id.timestamp_us == 12_500_000
    assert overlay.metadata == {"mqtt_message": message}
    assert overlay.content.time_slice.start == datetime(1970, 1, 1, 0, 0, 12, 500_000, tzinfo=timezone.utc)
    assert set(overlay.content.entities) == {EntityId("42")}
    classification = overlay.content.entities[EntityId("42")].observations[0].classification[0]
    assert classification.type == "human"
    assert classification.score == Score(0.9)
    assert broker.events[-1] == "stop"


def test_mqtt_delivers_message_arriving_during_decode(
    qtbot: QtBot, broker: _Broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finishing one decode must not erase a newer arrival or attach its metadata to the wrong scene."""
    decoder = ADFFrameV1Decoder()
    decode = decoder.decode
    first = MqttMessage(topic="analytics", payload='{"frame":{"timestamp":"1970-01-01T00:00:12Z"}}')
    second = MqttMessage(topic="analytics", payload='{"frame":{"timestamp":"1970-01-01T00:00:13Z"}}')

    def decode_with_arrival(payload: Any) -> Scene | None:
        if payload == first.payload:
            assert broker.message_callback is not None
            broker.message_callback(second)
        return decode(payload)

    monkeypatch.setattr(decoder, "decode", decode_with_arrival)
    source = _source(decoder=decoder)
    overlays: list[OverlayData] = []
    source.overlayReady.connect(overlays.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: broker.message_callback is not None)
        assert broker.message_callback is not None
        broker.message_callback(first)
        qtbot.waitUntil(lambda: len(overlays) == 2)
    finally:
        source.stop()
        source.deleteLater()

    assert [overlay.frame_id.timestamp_us for overlay in overlays] == [12_000_000, 13_000_000]
    assert [overlay.metadata for overlay in overlays] == [{"mqtt_message": first}, {"mqtt_message": second}]


def test_mqtt_source_reports_unavailable_data_source_and_stops_retrying(qtbot: QtBot, broker: _Broker) -> None:
    """An unknown data source is a terminal, actionable error listing the same channel's sources."""
    source = _source("com.axis.analytics_scene_description.v0.beta#1")
    errors: list[str] = []
    source.sourceError.connect(errors.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: len(errors) == 1)
        assert source.wait()
    finally:
        source.stop()
        source.deleteLater()

    assert "'com.axis.analytics_scene_description.v0.beta#1' is not available on device camera.local" in errors[0]
    assert "com.axis.scene.frame.v1#1" in errors[0]
    assert "com.axis.scene.frame.v1#2" not in errors[0]
    assert "--data-source with a matching --handler-type" in errors[0]
    assert broker.events == []


def test_mqtt_source_retries_refused_broker_until_connected(
    qtbot: QtBot, broker: _Broker, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Repeated partial starts clean up before capped retries, with credential-safe reasons and logs."""
    secret = "fake-secret /+?"
    failure = f"Connection refused for root: {secret}, {quote(secret, safe='')}, code 4"
    broker.start_results = [ConnectionRefusedError(failure) for _ in range(7)]
    source = _source()
    errors: list[str] = []
    events: list[str] = []
    delays: list[float] = []
    token = source.worker._stop_token
    wait = token.wait

    def record_retry(seconds: float) -> None:
        token.raise_if_cancelled()
        if seconds > 0.01:
            delays.append(seconds)
        else:
            wait(seconds)

    monkeypatch.setattr(token, "wait", record_retry)
    source.sourceError.connect(errors.append)
    source.sourceReconnecting.connect(events.append)
    source.sourceConnected.connect(lambda: events.append("connected"))
    try:
        assert source.play()
        qtbot.waitUntil(lambda: "connected" in events)
    finally:
        source.stop()
        assert source.wait()
        source.deleteLater()

    assert len(delays) == 7
    assert delays == sorted(delays) and delays[0] < delays[-1] <= 8.0
    assert events[-1] == "connected" and len(events) == 8
    assert all("Connection refused for root" in reason and "code 4" in reason for reason in events[:-1])
    assert broker.events == ["start", "started", "stop"] * 8
    assert errors == []
    for credential in (secret, quote(secret, safe="")):
        assert credential not in caplog.text
        assert all(credential not in reason for reason in events)


@pytest.mark.parametrize(
    "failure",
    [
        AuthenticationError("authentication_failed", "Device rejected root and fake-secret /+?"),
        ConfigurationError("invalid_port", "Invalid port number"),
        ConnectionError("Failed to connect to MQTT broker with code 4"),
        ConnectionError("Failed to connect to MQTT broker with code 5"),
    ],
)
def test_mqtt_rejection_is_terminal(qtbot: QtBot, broker: _Broker, failure: Exception) -> None:
    """Dependency rejections require manual Retry and release a partially started manager."""
    wrapped = RuntimeError(f"Failed to configure analytics publisher: {failure}")
    wrapped.__cause__ = failure
    broker.start_results = [wrapped]
    source = _source()
    errors: list[str] = []
    retries: list[str] = []
    source.sourceError.connect(errors.append)
    source.sourceReconnecting.connect(retries.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: bool(errors))
        assert source.wait()
        assert len(errors) == 1 and not retries
        assert "Connection failed" in errors[0]
        assert "fake-secret /+?" not in errors[0]
        assert broker.events == ["start", "started", "stop"]
    finally:
        source.retire()


def test_mqtt_retirement_interrupts_backoff(qtbot: QtBot, broker: _Broker, monkeypatch: pytest.MonkeyPatch) -> None:
    """A long retry wait is cancelled immediately, rather than keeping retired workers alive."""
    monkeypatch.setattr(mqtt_overlay_source, "RETRY_INITIAL_DELAY_S", 60.0)
    broker.start_results = [ConnectionRefusedError("Connection refused")]
    source = _source()
    retries: list[str] = []
    destroyed: list[bool] = []
    source.sourceReconnecting.connect(retries.append)
    source.destroyed.connect(lambda: destroyed.append(True))
    assert source.play()
    qtbot.waitUntil(lambda: bool(retries))
    source.retire()
    qtbot.waitUntil(lambda: bool(destroyed))
    assert broker.events == ["start", "started", "stop"]


def test_mqtt_html_error_page_keeps_retrying(qtbot: QtBot, broker: _Broker, monkeypatch: pytest.MonkeyPatch) -> None:
    """A camera answering 503 with HTML fails JSON decoding, a ValueError, yet stays retryable."""
    monkeypatch.setattr(mqtt_overlay_source, "RETRY_INITIAL_DELAY_S", 0.01)
    broker.start_results = [json.JSONDecodeError("Expecting value", "<html>503</html>", 0), None]
    source = _source()
    errors: list[str] = []
    events: list[str] = []
    source.sourceError.connect(errors.append)
    source.sourceReconnecting.connect(events.append)
    source.sourceConnected.connect(lambda: events.append("connected"))
    try:
        assert source.play()
        qtbot.waitUntil(lambda: "connected" in events)
    finally:
        source.retire()
    assert len(events) == 2 and not errors


def test_mqtt_stop_during_connection_setup_stops_the_client_after_start_returns(qtbot: QtBot, broker: _Broker) -> None:
    """Retirement keeps the GUI live and defers cleanup and deletion until blocked setup returns."""
    broker.release_start.clear()
    source = _source()
    destroyed: list[bool] = []
    callbacks: list[bool] = []
    connected: list[bool] = []
    source.sourceConnected.connect(lambda: connected.append(True))
    source.destroyed.connect(lambda: destroyed.append(True))
    try:
        assert source.play()
        qtbot.waitUntil(broker.start_entered.is_set)
        source.retire()
        QTimer.singleShot(0, lambda: callbacks.append(True))
        qtbot.waitUntil(lambda: bool(callbacks))
        assert broker.events == ["start"]
        assert not destroyed
    finally:
        broker.release_start.set()
        assert source.wait()

    qtbot.waitUntil(lambda: bool(destroyed))
    assert broker.events == ["start", "started", "stop"]
    assert not connected
