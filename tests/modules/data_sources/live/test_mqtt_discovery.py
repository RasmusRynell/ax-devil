"""Tests for shared MQTT source discovery."""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from ax_devil_device_api import DeviceConfig

from ax_devil.modules.data_sources.live import mqtt_discovery


class _ClientContext(AbstractContextManager[object]):
    def __init__(self, client: object) -> None:
        self._client = client

    def __enter__(self) -> object:
        return self._client

    def __exit__(self, *args: object) -> None:
        return None


def test_list_mqtt_data_source_keys_uses_device_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both callers receive only valid keys from the configured device."""
    analytics_mqtt = SimpleNamespace(
        get_data_sources=lambda: [{"key": "source/one"}, {"key": ""}, {}, {"key": "source/two"}]
    )
    client = SimpleNamespace(analytics_mqtt=analytics_mqtt)
    config = DeviceConfig.https(host="camera.local", username="root", password="pass")
    factory = MagicMock(return_value=_ClientContext(client))
    monkeypatch.setattr(mqtt_discovery, "Client", factory)

    keys = mqtt_discovery.list_mqtt_data_source_keys(config)

    factory.assert_called_once_with(config)
    assert keys == ("source/one", "source/two")
