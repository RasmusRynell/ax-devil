"""What the configured defaults say about a new live stream, read for the Add Live Stream dialog."""

from __future__ import annotations

from collections.abc import Container, Mapping
from typing import Any

from ax_devil.modules.settings.config_manager import integer_default
from ax_devil.modules.workspace.core import LiveOverlayMode, LiveStreamItem

_HANDLER_SECTIONS = {
    LiveOverlayMode.RTSP: "rtsp",
    LiveOverlayMode.MQTT: "analytics-mqtt",
    LiveOverlayMode.WEBSOCKET: "analytics-websocket",
}


def configured_overlay(
    live_defaults: Mapping[str, Any], installed_handlers: Container[str]
) -> tuple[LiveOverlayMode, str | None]:
    """Return the overlay mode and decoder the defaults name, or no overlay when that decoder is not installed."""
    try:
        mode = LiveOverlayMode.from_value(str(live_defaults.get("overlay_source", "none")))
    except ValueError:
        return LiveOverlayMode.NONE, None
    section = _HANDLER_SECTIONS.get(mode)
    handler = live_defaults[section].get("data_stream_handler") if section else None
    if handler is None or str(handler) not in installed_handlers:
        return LiveOverlayMode.NONE, None
    return mode, str(handler)


def connection_numbers(device_defaults: Mapping[str, Any], existing: LiveStreamItem | None) -> tuple[int, int, int]:
    """Return the device port, RTSP port and connection timeout: the saved item's own, else the configured defaults."""
    if existing is not None:
        return existing.device_port, existing.rtsp_port, existing.connection_timeout_s
    return (
        integer_default(device_defaults["port"], "defaults.device.port"),
        integer_default(device_defaults["rtsp_port"], "defaults.device.rtsp_port"),
        integer_default(device_defaults["connection_timeout_s"], "defaults.device.connection_timeout_s"),
    )
