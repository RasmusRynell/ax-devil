"""The Live Stream Item: a camera stream with optional live overlay settings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.device_connection import DeviceProtocol
from ax_devil.modules.settings.config_manager import (
    ConfigManager,
    default_protocol,
    expand_environment_reference,
    integer_default,
)
from ax_devil.modules.workspace.core.content import Content
from ax_devil.modules.workspace.core.item_base import WorkspaceItem, read_field, read_optional_str
from ax_devil.modules.workspace.core.resolution import ResolutionContext
from ax_devil.modules.workspace.core.source_specs import LiveOverlayMode

_LIVE_STR_FIELDS = (
    "host",
    "username",
    "password",
    "resolution",
    "mqtt_host",
    "mqtt_username",
    "mqtt_password",
    "analytics_data_source_key",
    "websocket_topic",
)
_LIVE_INT_FIELDS = ("camera_head", "mqtt_port", "websocket_channel_id", "device_port", "rtsp_port")
_LIVE_ENUM_FIELDS = ("overlay_mode", "device_api_protocol")


@dataclass(frozen=True, kw_only=True)
class LiveStreamItem(WorkspaceItem):
    """A camera stream with optional live overlay settings.

    The device and MQTT broker hosts, usernames, and passwords are stored as entered: a ``$VARIABLE`` reference stays a
    reference and is expanded only when the item resolves, using the config's rule. The connection timeout is not
    stored: the viewer reads it from the config each time it connects.
    """

    kind: ClassVar[str] = "live_stream"
    editable: ClassVar[bool] = True
    host: str
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    camera_head: int = 1
    resolution: str = "1280x720"
    stream_url: str | None = field(default=None, repr=False)
    overlay_mode: LiveOverlayMode = LiveOverlayMode.NONE
    handler_type: str | None = None
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = field(default="", repr=False)
    mqtt_password: str = field(default="", repr=False)
    analytics_data_source_key: str = ""
    device_api_protocol: DeviceProtocol = DeviceProtocol.HTTPS
    websocket_topic: str = ""
    websocket_channel_id: int = 1
    device_port: int = 0
    rtsp_port: int = 554

    def _fields_to_json(self, base_dir: Path | None) -> dict[str, Any]:
        """Return every field as stored; references stay references and the enums are their value strings."""
        return {
            name: getattr(self, name).value if name in _LIVE_ENUM_FIELDS else getattr(self, name)
            for name in (*_LIVE_STR_FIELDS, *_LIVE_INT_FIELDS, *_LIVE_ENUM_FIELDS, "stream_url", "handler_type")
        }

    @classmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path | None) -> dict[str, Any]:
        """Read every field, using the defaults for absent ones."""
        defaults = cls(host="")
        return {
            **{name: read_field(data, name, str, getattr(defaults, name)) for name in _LIVE_STR_FIELDS},
            **{name: read_field(data, name, int, getattr(defaults, name)) for name in _LIVE_INT_FIELDS},
            "stream_url": read_optional_str(data, "stream_url"),
            "handler_type": read_optional_str(data, "handler_type"),
            "overlay_mode": LiveOverlayMode.from_value(
                read_field(data, "overlay_mode", str, defaults.overlay_mode.value)
            ),
            "device_api_protocol": DeviceProtocol(
                read_field(data, "device_api_protocol", str, defaults.device_api_protocol.value)
            ),
        }

    @property
    def default_name(self) -> str:
        """Return the device host, with an environment reference expanded for display only."""
        return expand_environment_reference(self.host) or "Live stream"

    @property
    def hosts(self) -> tuple[str, ...]:
        """Return the camera host as entered and, when it is a ``$VARIABLE`` reference, as expanded."""
        return tuple(dict.fromkeys((self.host, self.expanded().host)))

    def expanded(self) -> LiveStreamItem:
        """Return this item with its environment references replaced by their current values."""
        return replace(
            self,
            host=expand_environment_reference(self.host),
            username=expand_environment_reference(self.username),
            password=expand_environment_reference(self.password),
            mqtt_host=expand_environment_reference(self.mqtt_host),
            mqtt_username=expand_environment_reference(self.mqtt_username),
            mqtt_password=expand_environment_reference(self.mqtt_password),
        )

    def _build_contents(self, context: ResolutionContext, token: CancelToken) -> Sequence[Content]:
        """Return the live stream, built from the expanded connection values."""
        values = self.expanded()
        return (
            context.intake.create_live_stream(
                display_name=self.default_name,
                host=values.host,
                username=values.username,
                password=values.password,
                camera_head=values.camera_head,
                resolution=values.resolution,
                stream_url=values.stream_url,
                overlay_mode=values.overlay_mode,
                handler_type=values.handler_type,
                mqtt_host=values.mqtt_host,
                mqtt_port=values.mqtt_port,
                mqtt_username=values.mqtt_username,
                mqtt_password=values.mqtt_password,
                analytics_data_source_key=values.analytics_data_source_key,
                device_api_protocol=values.device_api_protocol,
                websocket_topic=values.websocket_topic,
                websocket_channel_id=values.websocket_channel_id,
                device_port=values.device_port,
                rtsp_port=values.rtsp_port,
            ),
        )


def live_stream_item_from_defaults(
    *,
    label: str = "",
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
    camera_head: int | None = None,
    resolution: str | None = None,
    overlay: str | None = None,
    handler_type: str | None = None,
    mqtt_host: str | None = None,
    mqtt_port: int | None = None,
    mqtt_username: str | None = None,
    mqtt_password: str | None = None,
    analytics_data_source_key: str | None = None,
    device_api_protocol: DeviceProtocol | None = None,
    websocket_topic: str | None = None,
    websocket_channel_id: int | None = None,
) -> LiveStreamItem:
    """Build a ``LiveStreamItem`` named *label* from the config's stream defaults, with optional overrides.

    Device and MQTT broker connection defaults are taken as written in the config, so a ``$VARIABLE`` reference stays
    a reference in the item. The protocol and RTSP port come from the config unless given.
    """
    cfg = ConfigManager()
    defaults = cfg.get("defaults", {}) or {}
    live = defaults.get("live_stream", {}) or {}
    rtsp = live.get("rtsp", {}) or {}
    mqtt = live.get("analytics-mqtt", {}) or {}
    websocket = live.get("analytics-websocket", {}) or {}
    raw_defaults = cfg.get_raw("defaults", {}) or {}
    device = raw_defaults.get("device", {}) or {}
    raw_mqtt = (raw_defaults.get("live_stream", {}) or {}).get("analytics-mqtt", {}) or {}

    resolved_overlay = LiveOverlayMode.from_value(overlay or str(live.get("overlay_source", "none")))
    resolved_handler = handler_type
    if resolved_handler is None and resolved_overlay is LiveOverlayMode.RTSP:
        resolved_handler = rtsp.get("data_stream_handler")
    if resolved_handler is None and resolved_overlay is LiveOverlayMode.MQTT:
        resolved_handler = mqtt.get("data_stream_handler")
    if resolved_handler is None and resolved_overlay is LiveOverlayMode.WEBSOCKET:
        resolved_handler = websocket.get("data_stream_handler")

    return LiveStreamItem(
        label=label,
        host=host or str(device.get("host") or ""),
        username=username or str(device.get("username") or ""),
        password=password or str(device.get("password") or ""),
        camera_head=camera_head if camera_head is not None else integer_default(rtsp.get("camera_head", 1), 1),
        resolution=resolution or rtsp.get("resolution", "1280x720"),
        overlay_mode=resolved_overlay,
        handler_type=resolved_handler,
        mqtt_host=mqtt_host or str(raw_mqtt.get("broker_host") or ""),
        mqtt_port=mqtt_port if mqtt_port is not None else integer_default(mqtt.get("broker_port", 1883), 1883),
        mqtt_username=mqtt_username or str(raw_mqtt.get("broker_username") or ""),
        mqtt_password=mqtt_password or str(raw_mqtt.get("broker_password") or ""),
        analytics_data_source_key=analytics_data_source_key or mqtt.get("data_source_key", ""),
        device_api_protocol=device_api_protocol or default_protocol(cfg),
        websocket_topic=websocket_topic or websocket.get("topic", ""),
        websocket_channel_id=(
            websocket_channel_id
            if websocket_channel_id is not None
            else integer_default(websocket.get("channel_id", 1), 1)
        ),
        rtsp_port=integer_default(defaults.get("device", {}).get("rtsp_port"), 554),
    )
