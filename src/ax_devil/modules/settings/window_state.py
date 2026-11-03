"""Persist desktop window state separately from application configuration."""

from pathlib import Path

from PySide6.QtCore import QSettings

from ax_devil.modules.settings.config_manager import ConfigManager


def window_state_settings() -> QSettings:
    """Return Qt's window-size store in the configured application storage directory."""
    storage = ConfigManager().get("storage")
    path = Path(storage["base_dir"]) / "window-state.ini"
    return QSettings(str(path), QSettings.Format.IniFormat)
