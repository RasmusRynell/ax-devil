"""Tests for shared MQTT source discovery."""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import SimpleNamespace

import pytest
from ax_devil_device_api import DeviceConfig

from ax_devil.modules.data_sources.live import mqtt_discovery


class _ClientContext(AbstractContextManager[object]):
    def __init__(self, config: DeviceConfig, cameras: dict[str, object]) -> None:
        self._client = cameras[config.host]

    def __enter__(self) -> object:
        return self._client

    def __exit__(self, *args: object) -> None:
        return None


def test_list_mqtt_data_source_keys_uses_device_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both callers receive only valid keys from the configured device."""
    analytics_mqtt = SimpleNamespace(
        get_data_sources=lambda: [{"key": "source/one"}, {"key": ""}, {}, {"key": "source/two"}]
    )
    other_mqtt = SimpleNamespace(get_data_sources=lambda: [{"key": "other/camera"}])
    cameras: dict[str, object] = {
        "camera.local": SimpleNamespace(analytics_mqtt=analytics_mqtt),
        "other.local": SimpleNamespace(analytics_mqtt=other_mqtt),
    }
    config = DeviceConfig.https(host="camera.local", username="root", password="pass")
    monkeypatch.setattr(mqtt_discovery, "Client", lambda device_config: _ClientContext(device_config, cameras))

    keys = mqtt_discovery.list_mqtt_data_source_keys(config)

    assert keys == ("source/one", "source/two")
