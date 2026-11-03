"""Query the analytics inputs a device can publish over MQTT."""

from typing import Any

from ax_devil_device_api import Client, DeviceConfig


def list_mqtt_data_source_keys(config: DeviceConfig) -> tuple[str, ...]:
    """Return available source keys for discovery and runtime validation."""
    with Client(config) as client:
        sources: list[dict[str, Any]] = client.analytics_mqtt.get_data_sources()
    return tuple(source["key"] for source in sources if isinstance(source.get("key"), str) and source["key"])
