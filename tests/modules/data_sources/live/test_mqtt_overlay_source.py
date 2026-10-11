"""Tests for MQTT overlay connection errors and lifecycle."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pytest
from ax_devil_mqtt import MqttMessage
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import OverlayData
from ax_devil.modules.data_sources.live import mqtt_discovery, mqtt_overlay_source
from ax_devil.modules.data_sources.live.mqtt_overlay_source import MQTTOverlaySource
from ax_devil.modules.scene.model import EntityId, Scene
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
        broker_username="",
        broker_password="",
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
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
        source.deleteLater()

    assert len(overlays) == 1
    overlay = overlays[0]
    assert overlay.source_id == "test-camera"
    assert overlay.frame_id.timestamp_monotime_us == 12_500_000
    assert overlay.metadata == {"mqtt_message": message}
    assert overlay.content.time_slice.start == datetime(1970, 1, 1, 0, 0, 12, 500_000, tzinfo=timezone.utc)
    assert set(overlay.content.entities) == {EntityId("42")}
    classification = overlay.content.entities[EntityId("42")].observations[0].classification[0]
    assert classification.type == "human"
    assert classification.score.value == 0.9
    assert broker.events[-1] == "stop"


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


def test_mqtt_source_retries_refused_broker_until_connected(qtbot: QtBot, broker: _Broker) -> None:
    """A refused broker is retried, so it reports reconnecting rather than a terminal error."""
    broker.start_results = [ConnectionRefusedError("Connection refused"), None]
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
        source.stop()
        source.deleteLater()

    assert events == ["Connection failed: Connection refused", "connected"]
    assert errors == []


def test_mqtt_stop_during_connection_setup_stops_the_client_after_start_returns(qtbot: QtBot, broker: _Broker) -> None:
    """Stopping while the broker connection is starting never tears the client down underneath start()."""
    broker.release_start.clear()
    source = _source()
    try:
        assert source.play()
        assert broker.start_entered.wait(5)
        release = threading.Timer(0.1, broker.release_start.set)
        release.start()
        source.stop()
        release.join()
    finally:
        broker.release_start.set()
        source.stop()
        source.deleteLater()

    assert broker.events == ["start", "started", "stop"]
