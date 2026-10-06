from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, cast

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.paths import (
    AX_DEVIL_HOME,
    CACHE_DIR,
    DEFAULT_CONFIG_PATH,
    LOGS_DIR,
    RENDER_CATALOGS_DIR,
)
from ax_devil.modules.settings.playback_settings import VideoCacheBudget

logger = get_logger(__name__)

CONFIG_VERSION: str = "2.0"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "storage": {
        "base_dir": str(AX_DEVIL_HOME),
        "cache_dir": str(CACHE_DIR),
        "logs_dir": str(LOGS_DIR),
        "render_catalogs_dir": str(RENDER_CATALOGS_DIR),
    },
    "ui": {
        "theme": "auto",
        "text_size": "system",
        "quick_setup_done": False,
        "window": {
            "custom_frame": True,
        },
    },
    "shortcuts": {},
    "settings": {
        "playback": {"video_cache_total_mib": VideoCacheBudget().config_value},
        "appearance": {
            "graphics_acceleration": "auto",
        },
        "overlay_interaction": OverlayPreference.config_value(OverlayPreference.from_config(None)),
    },
    "defaults": {
        "device": {
            "host": "$AX_DEVIL_TARGET_ADDR",
            "username": "$AX_DEVIL_TARGET_USER",
            "password": "$AX_DEVIL_TARGET_PASS",
        },
        "live_stream": {
            "rtsp": {
                "camera_head": 1,
                "resolution": "1280x720",
                "data_stream_handler": "ONVIF_XML",
            },
            "analytics-mqtt": {
                "broker_host": "$AX_DEVIL_MQTT_BROKER_ADDR",
                "broker_username": "$AX_DEVIL_MQTT_BROKER_USER",
                "broker_password": "$AX_DEVIL_MQTT_BROKER_PASS",
                "broker_port": 1883,
                "data_source_key": "com.axis.scene.frame.v1#1",
                "data_stream_handler": "ADF_V1_FRAME",
                "device_api_protocol": "https",
            },
            "analytics-websocket": {
                "topic": "com.axis.scene.frame.v1",
                "channel_id": 1,
                "data_stream_handler": "ADF_V1_FRAME",
                "device_api_protocol": "https",
            },
            "overlay_source": "websocket",
        },
    },
}

MAX_UNSUPPORTED_CONFIG_PATHS_TO_LOG = 8


def _resolve_runtime_value(value: Any) -> Any:
    """Return a runtime projection with ``~`` and ``$VAR`` values expanded."""
    missing_env_vars: set[str] = set()

    def _resolve_nested(current: Any) -> Any:
        if isinstance(current, dict):
            return {key: _resolve_nested(nested_value) for key, nested_value in current.items()}
        if isinstance(current, list):
            return [_resolve_nested(item) for item in current]
        if isinstance(current, str):
            if current.startswith("~"):
                expanded_path = Path(current).expanduser().resolve(strict=False)
                logger.debug("Resolved user directory in config value")
                return str(expanded_path)
            if current.startswith("$"):
                env_var = current[1:]
                resolved_value = os.getenv(env_var, "")
                if resolved_value:
                    logger.debug(f"Resolved env var ${env_var}")
                    return resolved_value
                missing_env_vars.add(env_var)
                return ""
        return current

    resolved_value = _resolve_nested(value)
    if missing_env_vars:
        missing_env_var_list = ", ".join(sorted(missing_env_vars))
        logger.warning(
            f"Config references unset environment variables ({missing_env_var_list}). "
            "Using empty strings for those values."
        )
    return resolved_value


def integer_default(value: object, default: int) -> int:
    """Read a resolved numeric default, falling back if its environment value is missing or invalid."""
    try:
        return int(str(value))
    except ValueError:
        logger.warning(f"Invalid numeric default; using {default}.")
        return default


def _merge_defaults(config: dict[str, Any], defaults: Mapping[str, Any], *, key_path: str = "config") -> dict[str, Any]:
    """Fill missing keys from *defaults* into *config* recursively.

    This only fills missing keys. Existing values are preserved as-is.
    """
    for key, default_value in defaults.items():
        child_path = f"{key_path}.{key}"
        if key not in config:
            config[key] = copy.deepcopy(default_value)
        elif isinstance(default_value, Mapping):
            loaded_value = config[key]
            if not isinstance(loaded_value, Mapping):
                raise ValueError(
                    f"Cannot merge config branch at '{child_path}': expected object, got {type(loaded_value).__name__}."
                )
            _merge_defaults(cast(dict[str, Any], loaded_value), default_value, key_path=child_path)
    return config


def _collect_unsupported_key_paths(
    config: Mapping[str, Any], supported_shape: Mapping[str, Any], *, key_path: str = "config"
) -> list[str]:
    """Return dotted key paths in *config* that are not part of *supported_shape*."""
    unsupported_paths: list[str] = []
    for key, value in config.items():
        child_path = f"{key_path}.{key}"
        if key not in supported_shape:
            unsupported_paths.append(child_path)
            continue

        supported_value = supported_shape[key]
        if isinstance(supported_value, Mapping) and isinstance(value, Mapping):
            unsupported_paths.extend(
                _collect_unsupported_key_paths(cast(Mapping[str, Any], value), supported_value, key_path=child_path)
            )
    return unsupported_paths


def _unsupported_config_key_paths(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Return sorted unsupported config key paths for the current config shape."""
    return tuple(sorted(_collect_unsupported_key_paths(config, DEFAULT_CONFIG)))


def _warn_for_unsupported_keys(config: Mapping[str, Any]) -> None:
    """Log a single concise warning when unsupported keys are present."""
    unsupported_paths = _unsupported_config_key_paths(config)
    if not unsupported_paths:
        return

    displayed_paths = unsupported_paths[:MAX_UNSUPPORTED_CONFIG_PATHS_TO_LOG]
    remaining_count = len(unsupported_paths) - len(displayed_paths)
    suffix = f" (+{remaining_count} more)" if remaining_count > 0 else ""
    logger.warning(
        "Config contains unsupported keys that are preserved but ignored by this version: "
        f"{', '.join(displayed_paths)}{suffix}"
    )


def _version_major(version: Any) -> str:
    if not isinstance(version, str) or "." not in version:
        raise ValueError(f"Invalid config version {version!r}; expected 'major.minor'.")
    major, _minor = version.split(".", 1)
    if not major:
        raise ValueError(f"Invalid config version {version!r}; expected 'major.minor'.")
    return major


def finalize_loaded_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate supported branches, fill defaults, and preserve the raw config document."""
    _merge_defaults(config, DEFAULT_CONFIG)
    _warn_for_unsupported_keys(config)
    return config


def load_config(path: Path, *, create_if_missing: bool = True) -> dict[str, Any]:
    """Load and validate configuration from disk."""
    logger.info(f"Loading config from {path}")

    if not path.exists() or not path.is_file():
        if not create_if_missing:
            raise FileNotFoundError(f"Config file does not exist: {path}")

        logger.info(f"Config file {path} does not exist, creating new config.")
        default_config = copy.deepcopy(DEFAULT_CONFIG)
        save_config(default_config, path)
        return finalize_loaded_config(default_config)

    config = json.loads(path.read_text(encoding="utf-8"))
    config_version = config.get("version")
    if _version_major(config_version) != _version_major(CONFIG_VERSION):
        raise ValueError(
            f"Unsupported config major version {config_version!r}; expected major {_version_major(CONFIG_VERSION)!r}. "
            "Replace the config file."
        )

    return finalize_loaded_config(config)


def save_config(config: dict[str, Any], path: Path) -> None:
    """Atomically replace configuration, reporting failures to the caller."""
    path = path.resolve()
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            json.dump(config, f, indent=4)
        temporary.replace(path)
        logger.info(f"Saved config version {config.get('version')} to {path}")
    except Exception as exc:
        logger.error(f"Error saving config: {exc}")
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ConfigManager:
    """Singleton manager for application configuration."""

    _instance = None

    def __new__(cls) -> ConfigManager:
        if cls._instance is None:
            logger.debug("Creating new ConfigManager instance")
            cls._instance = super().__new__(cls)
        else:
            logger.debug("Returning existing ConfigManager instance")
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_initialized"):
            self._config_path = DEFAULT_CONFIG_PATH
            self._create_if_missing = True
            self._raw_config: dict[str, Any] = {}
            self._loaded = False
            self._active_storage: dict[str, Any] | None = None
            self._initialized = True

    def _load_if_needed(self) -> None:
        if not self._loaded:
            self._raw_config = load_config(self._config_path, create_if_missing=self._create_if_missing)
            self._loaded = True

    def get(self, key: str, default: Any = None) -> Any:
        self._load_if_needed()
        if key == "storage" and self._active_storage is not None:
            return copy.deepcopy(self._active_storage)
        if key not in self._raw_config:
            result = default
        else:
            result = _resolve_runtime_value(copy.deepcopy(self._raw_config[key]))
        logger.debug(f"ConfigManager getting config key: {key}")
        return result

    def get_raw(self, key: str, default: Any = None) -> Any:
        """Return a top-level value from the raw config document."""
        self._load_if_needed()
        return self._raw_config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._load_if_needed()
        logger.debug(f"ConfigManager setting config key: {key}")
        self._raw_config[key] = value

    def save(self) -> None:
        self._load_if_needed()
        save_config(self._raw_config, self._config_path)

    @property
    def config_path(self) -> Path:
        """Return the file selected at launch."""
        return self._config_path

    def activate_storage(self) -> None:
        """Hold startup storage locations until restart while allowing saved preferences to change."""
        self._active_storage = None
        self._active_storage = self.get("storage", {})

    def reset(self) -> None:
        self._load_if_needed()
        logger.info(f"ConfigManager Resetting config to defaults at {self._config_path}")
        self._raw_config = copy.deepcopy(DEFAULT_CONFIG)
        save_config(self._raw_config, self._config_path)

    def ensure_storage_directories(self) -> None:
        """Ensure configured storage directories exist."""
        self._load_if_needed()
        storage_config = self.get("storage", {})
        for key in ("base_dir", "cache_dir", "logs_dir", "render_catalogs_dir"):
            directory = storage_config.get(key)
            if not isinstance(directory, str) or not directory:
                raise ValueError(f"Invalid storage directory for '{key}': {directory!r}")
            Path(directory).expanduser().mkdir(parents=True, exist_ok=True)

    def set_config_path(self, path: Path, *, create_if_missing: bool = True) -> None:
        """Set a new config path and reload policy."""
        previous_path = self._config_path
        previous_create_if_missing = self._create_if_missing
        if previous_path == path and previous_create_if_missing == create_if_missing:
            logger.debug(
                "ConfigManager config path unchanged "
                f"(path={path}, create_if_missing={create_if_missing}); keeping loaded config state"
            )
            return

        logger.info(
            "ConfigManager changing config path "
            f"from {previous_path} (create_if_missing={previous_create_if_missing}) "
            f"to {path} (create_if_missing={create_if_missing}); invalidating loaded config"
        )
        self._config_path = path
        self._create_if_missing = create_if_missing
        self._loaded = False
        self._active_storage = None
