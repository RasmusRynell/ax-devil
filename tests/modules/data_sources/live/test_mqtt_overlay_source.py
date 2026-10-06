"""Tests for MQTT overlay connection errors and lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from ax_devil_mqtt import MqttMessage
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import OverlayData
from ax_devil.modules.data_sources.live import mqtt_discovery, mqtt_overlay_source
from ax_devil.modules.data_sources.live.mqtt_overlay_source import MQTTOverlaySource, MQTTWorker
from ax_devil.modules.scene.model import EntityId
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


def test_mqtt_delivery_recovers_after_malformed_payload(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real decoding and Qt emission preserve camera time after a bad transport packet."""
    monkeypatch.setattr(mqtt_discovery, "Client", _DeviceClient)
    manager = MagicMock()
    monkeypatch.setattr(mqtt_overlay_source, "AxisAnalyticsMqttClient", manager)
    source = MQTTOverlaySource(
        broker_host="broker.local",
        broker_port=1883,
        broker_username="",
        broker_password="",
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        analytics_data_source_key="com.axis.scene.frame.v1#1",
        decoder=ADFFrameV1Decoder(),
        handler_type="ADF_V1_FRAME",
        source_id="test-camera",
    )
    overlays: list[OverlayData] = []
    source.overlayReady.connect(overlays.append)
    worker = source.worker
    try:
        worker._connect()
        manager.return_value.start.assert_called_once_with()
        receive = manager.call_args.kwargs["message_callback"]
        receive(MqttMessage(topic="analytics", payload="{invalid"))
        assert worker.run_loop()
        assert overlays == []

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
        receive(message)
        assert worker.run_loop()
        assert len(overlays) == 1
        overlay = overlays[0]
        assert overlay.source_id == "test-camera"
        assert overlay.frame_id.sequence_id == 2
        assert overlay.frame_id.timestamp_monotime_us == 12_500_000
        assert overlay.metadata == {"mqtt_message": message}
        assert overlay.content.time_slice.start == datetime(1970, 1, 1, 0, 0, 12, 500_000, tzinfo=timezone.utc)
        assert set(overlay.content.entities) == {EntityId("42")}
        classification = overlay.content.entities[EntityId("42")].observations[0].classification[0]
        assert classification.type == "human"
        assert classification.score.value == 0.9
        assert worker.run_loop()
        assert len(overlays) == 1
    finally:
        source.stop()
        source.deleteLater()
    manager.return_value.stop.assert_called_once_with()


def test_mqtt_worker_reports_unavailable_source_and_stops_retrying(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mqtt_discovery, "Client", _DeviceClient)
    manager = MagicMock()
    monkeypatch.setattr(mqtt_overlay_source, "AxisAnalyticsMqttClient", manager)
    worker = MQTTWorker(
        broker_host="broker.local",
        broker_port=1883,
        broker_username="",
        broker_password="",
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        analytics_data_source_key="com.axis.analytics_scene_description.v0.beta#1",
        decoder=MagicMock(),
    )
    errors: list[str] = []
    worker.sourceError.connect(errors.append)
    worker._running = True

    worker._connect()

    assert worker.is_playing() is False
    assert len(errors) == 1
    assert "'com.axis.analytics_scene_description.v0.beta#1' is not available on device camera.local" in errors[0]
    assert "com.axis.scene.frame.v1#1" in errors[0]
    assert "com.axis.scene.frame.v1#2" not in errors[0]
    assert "--data-source with a matching --handler-type" in errors[0]
    manager.assert_not_called()


def test_mqtt_worker_reports_retried_connection_failure_then_connection(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused broker is retried, so it reports reconnecting rather than a terminal error."""
    monkeypatch.setattr(mqtt_discovery, "Client", _DeviceClient)
    manager = MagicMock()
    manager.return_value.start.side_effect = [ConnectionRefusedError("Connection refused"), None]
    monkeypatch.setattr(mqtt_overlay_source, "AxisAnalyticsMqttClient", manager)
    worker = MQTTWorker(
        broker_host="broker.local",
        broker_port=1883,
        broker_username="",
        broker_password="",
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        analytics_data_source_key="com.axis.scene.frame.v1#1",
        decoder=MagicMock(),
    )
    errors: list[str] = []
    reconnecting: list[str] = []
    connected: list[bool] = []
    worker.sourceError.connect(errors.append)
    worker.sourceReconnecting.connect(reconnecting.append)
    worker.sourceConnected.connect(lambda: connected.append(True))
    worker._running = True

    worker._connect()

    assert reconnecting == ["Connection failed: Connection refused"]
    assert connected == []
    assert worker.is_playing()

    worker._connect()

    assert connected == [True]
    assert errors == []


def test_mqtt_overlay_stop_waits_for_worker_before_final_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = MagicMock()
    events: list[str] = []
    worker.request_stop.side_effect = lambda: events.append("request-stop")
    worker.stop.side_effect = lambda: events.append("stop")
    worker.wait.side_effect = lambda: events.append("wait")
    monkeypatch.setattr(mqtt_overlay_source, "MQTTWorker", MagicMock(return_value=worker))
    source = MQTTOverlaySource(
        broker_host="broker.local",
        broker_port=1883,
        broker_username="",
        broker_password="",
        device_host="camera.local",
        device_username="root",
        device_password="pass",
        device_api_protocol="https",
        analytics_data_source_key="com.axis.scene.frame.v1#1",
        decoder=MagicMock(),
        handler_type="ADF_V1_FRAME",
    )

    source.stop()

    assert events == ["request-stop", "wait", "stop"]
    worker.wait.assert_called_once_with()
    worker.set_overlay_ready_signal.assert_any_call(None)
