"""Editable connection defaults and storage preferences, preserving raw environment references."""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings, SettingsState
from ax_devil.modules.workspace.content import LiveOverlayMode


def is_environment_reference(value: str) -> bool:
    """Return whether a raw preference names an environment variable."""
    return re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", value) is not None


@dataclass(frozen=True)
class ConfigField:
    """One supported preference with its label, choices and validation."""

    path: str
    label: str
    choices: tuple[tuple[str, str], ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    secret: bool = False
    directory: bool = False
    decoder: bool = False

    def parse(self, text: str) -> str | int:
        """Validate edited values and numeric references, preserving their raw bindings."""
        value = text if self.secret else text.strip()
        numeric_value = value
        reference = value.startswith("$")
        if reference:
            if not is_environment_reference(value):
                raise ValueError(f"{self.label}: use an environment reference such as $VARIABLE_NAME.")
            if self.directory and not os.getenv(value[1:], "").strip():
                raise ValueError(f"{self.label}: the environment reference must resolve to a non-empty folder path.")
            if self.minimum is None:
                return value
            numeric_value = os.getenv(value[1:], "")
        if self.minimum is not None and self.maximum is not None:
            try:
                number = int(numeric_value)
            except ValueError:
                raise ValueError(
                    f"{self.label}: enter a whole number; environment references must resolve to one."
                ) from None
            if not self.minimum <= number <= self.maximum:
                raise ValueError(f"{self.label}: enter a number from {self.minimum} to {self.maximum}.")
            return value if reference else number
        if self.choices:
            value = value.lower()
            if value not in {choice for _label, choice in self.choices}:
                raise ValueError(f"{self.label}: choose one of the listed values or use an environment reference.")
        if self.directory and not value:
            raise ValueError(f"{self.label}: choose a folder or use an environment reference.")
        if self.path.endswith(".resolution") and re.fullmatch(r"[1-9]\d*x[1-9]\d*", value) is None:
            raise ValueError("Resolution: enter width x height, such as 1280x720.")
        return value


@dataclass(frozen=True)
class ConfigSection:
    """A group of related fields shown together in Settings."""

    title: str
    fields: tuple[ConfigField, ...]
    requires_restart: bool = False


CONNECTION_SECTIONS = (
    ConfigSection(
        "Device",
        (
            ConfigField("defaults.device.host", "Host"),
            ConfigField("defaults.device.username", "Username"),
            ConfigField("defaults.device.password", "Password", secret=True),
        ),
    ),
    ConfigSection(
        "Video and overlay",
        (
            ConfigField("defaults.live_stream.rtsp.camera_head", "Camera head", minimum=1, maximum=9999),
            ConfigField("defaults.live_stream.rtsp.resolution", "Resolution"),
            ConfigField(
                "defaults.live_stream.overlay_source",
                "Overlay mode",
                choices=tuple((mode.display_name, mode.value) for mode in LiveOverlayMode),
            ),
            ConfigField("defaults.live_stream.rtsp.data_stream_handler", "RTSP decoder", decoder=True),
        ),
    ),
    ConfigSection(
        "MQTT analytics",
        (
            ConfigField("defaults.live_stream.analytics-mqtt.broker_host", "Broker host"),
            ConfigField("defaults.live_stream.analytics-mqtt.broker_port", "Broker port", minimum=1, maximum=65535),
            ConfigField("defaults.live_stream.analytics-mqtt.broker_username", "Broker username"),
            ConfigField("defaults.live_stream.analytics-mqtt.broker_password", "Broker password", secret=True),
            ConfigField("defaults.live_stream.analytics-mqtt.data_source_key", "Data source"),
            ConfigField("defaults.live_stream.analytics-mqtt.data_stream_handler", "Decoder", decoder=True),
            ConfigField(
                "defaults.live_stream.analytics-mqtt.device_api_protocol",
                "Device protocol",
                choices=(("HTTPS", "https"), ("HTTP", "http")),
            ),
        ),
    ),
    ConfigSection(
        "DataHub WebSocket analytics",
        (
            ConfigField("defaults.live_stream.analytics-websocket.topic", "Topic"),
            ConfigField(
                "defaults.live_stream.analytics-websocket.channel_id", "Topic channel", minimum=1, maximum=9999
            ),
            ConfigField("defaults.live_stream.analytics-websocket.data_stream_handler", "Decoder", decoder=True),
            ConfigField(
                "defaults.live_stream.analytics-websocket.device_api_protocol",
                "Device protocol",
                choices=(("HTTPS", "https"), ("HTTP", "http")),
            ),
        ),
    ),
)

STORAGE_SECTION = ConfigSection(
    "Storage locations",
    (
        ConfigField("storage.base_dir", "Window state folder", directory=True),
        ConfigField("storage.cache_dir", "Cache folder", directory=True),
        ConfigField("storage.logs_dir", "Logs folder", directory=True),
        ConfigField("storage.render_catalogs_dir", "Render catalogs folder", directory=True),
    ),
    requires_restart=True,
)


def field_value(config: ConfigManager, field: ConfigField) -> str:
    """Return the saved value as editor text, keeping environment references literal."""
    root, *keys = field.path.split(".")
    value: Any = config.get_raw(root)
    for key in keys:
        value = value[key]
    return str(value)


def configuration_updates(config: ConfigManager, edits: Mapping[ConfigField, str]) -> dict[str, Any]:
    """Validate all edited fields and return updated roots without changing the live config."""
    roots: dict[str, Any] = {}
    for field, text in edits.items():
        value = field.parse(text)
        root, *keys = field.path.split(".")
        if root not in roots:
            roots[root] = copy.deepcopy(config.get_raw(root))
        branch = roots[root]
        for key in keys[:-1]:
            branch = branch[key]
        branch[keys[-1]] = value
    return roots


def apply_preferences(
    config: ConfigManager,
    settings: GlobalSettings,
    snapshot: SettingsState,
    edits: Mapping[ConfigField, str],
) -> None:
    """Validate and save preferences before publishing any runtime changes."""
    updates = configuration_updates(config, edits)
    previous = {root: copy.deepcopy(config.get_raw(root, {})) for root in (*updates, "ui", "settings")}
    try:
        for root, value in updates.items():
            config.set(root, value)
        settings.save_to_config(config, snapshot=snapshot)
        config.save()
    except Exception:
        for root, value in previous.items():
            config.set(root, value)
        raise
    settings.apply_snapshot(snapshot)


def save_overlay_preference(
    config: ConfigManager,
    settings: GlobalSettings,
    preference: OverlayPreference,
    enabled: bool,
) -> None:
    """Save one overlay preference, then apply it to open viewers."""
    snapshot = settings.snapshot()
    overlays = snapshot.enabled_overlays
    snapshot = replace(snapshot, enabled_overlays=overlays | {preference} if enabled else overlays - {preference})
    apply_preferences(config, settings, snapshot, {})
