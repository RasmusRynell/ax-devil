"""Global application settings with reactive change propagation.

Provides a singleton ``GlobalSettings`` QObject that sits between
``ConfigManager`` (file I/O) and the UI.  Settings are mutable at runtime
and emit Qt signals on change so consumers can react without polling.
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.playback_settings import VideoCacheBudget, detect_available_memory_bytes
from ax_devil.modules.settings.theme_mode import ThemeMode

logger = get_logger(__name__)


class GlobalSettings(QObject):
    """Reactive singleton for runtime-mutable application settings.

    Each concrete setting has a typed signal (e.g. ``overlay_preference_changed``)
    for direct consumers.  The generic ``setting_changed`` signal fires for
    every mutation, enabling loose coupling for future plugins or UI that
    wants to observe all changes.
    """

    _instance: GlobalSettings | None = None

    # Per-setting signals
    overlay_preference_changed = Signal(object, bool)  # OverlayPreference, enabled
    video_cache_budget_changed = Signal(object)
    theme_changed = Signal(str)

    # Generic signal: (setting_dotted_key, new_value)
    setting_changed = Signal(str, object)

    def __new__(cls, parent: QObject | None = None) -> GlobalSettings:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, parent: QObject | None = None) -> None:
        if hasattr(self, "_initialized"):
            return
        super().__init__(parent)
        self._initialized = True
        self.available_memory_bytes = detect_available_memory_bytes()
        self._video_cache_budget = VideoCacheBudget()
        self._overlay_preferences = OverlayPreference.from_config(None)
        self._graphics_acceleration = GraphicsAcceleration.AUTO
        self._theme = ThemeMode.AUTO
        self._custom_frame = True
        # Capture the inherited override before startup applies our own presentation default.
        self.graphics_acceleration_override = os.environ.get("QT_WIDGETS_RHI")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def video_cache_budget(self) -> VideoCacheBudget:
        """Return the total memory preference shared by all offline sources."""
        return self._video_cache_budget

    @video_cache_budget.setter
    def video_cache_budget(self, value: VideoCacheBudget) -> None:
        if value == self._video_cache_budget:
            return
        self._video_cache_budget = value
        self.video_cache_budget_changed.emit(value)
        self.setting_changed.emit("playback.video_cache_total_mib", value.config_value)

    def is_overlay_enabled(self, preference: OverlayPreference) -> bool:
        """Return whether viewers show *preference*."""
        return self._overlay_preferences[preference]

    def set_overlay_enabled(self, preference: OverlayPreference, enabled: bool) -> None:
        """Turn *preference* on or off for every viewer."""
        if enabled == self._overlay_preferences[preference]:
            return
        self._overlay_preferences[preference] = enabled
        logger.debug(f"{preference.value} changed to {enabled}")
        self.overlay_preference_changed.emit(preference, enabled)
        self.setting_changed.emit(f"overlay_interaction.{preference.value}", enabled)

    @property
    def custom_frame(self) -> bool:
        """Whether to use the application's title bar after the next restart."""
        return self._custom_frame

    @custom_frame.setter
    def custom_frame(self, value: bool) -> None:
        if value == self._custom_frame:
            return
        self._custom_frame = value
        self.setting_changed.emit("appearance.custom_frame", value)

    @property
    def theme(self) -> ThemeMode:
        """Return the application appearance preference."""
        return self._theme

    @theme.setter
    def theme(self, value: ThemeMode) -> None:
        if value == self._theme:
            return
        self._theme = value
        self.theme_changed.emit(value.value)
        self.setting_changed.emit("appearance.theme", value.value)

    @property
    def graphics_acceleration(self) -> GraphicsAcceleration:
        """Return the saved presentation preference, applied on the next startup."""
        return self._graphics_acceleration

    @graphics_acceleration.setter
    def graphics_acceleration(self, value: GraphicsAcceleration) -> None:
        if value == self._graphics_acceleration:
            return
        self._graphics_acceleration = value
        self.setting_changed.emit("appearance.graphics_acceleration", value.value)

    # ------------------------------------------------------------------
    # Config round-trip
    # ------------------------------------------------------------------

    def load_from_config(self, config_manager: ConfigManager) -> None:
        """Read settings from ``ConfigManager`` without emitting signals."""
        settings: dict[str, Any] = config_manager.get("settings", {}) or {}
        ui = config_manager.get("ui", {}) or {}
        self._theme = ThemeMode.from_config(ui.get("theme", "auto"))
        self._custom_frame = bool((ui.get("window", {}) or {}).get("custom_frame", True))
        playback = settings.get("playback", {}) or {}
        self._video_cache_budget = VideoCacheBudget.from_config(playback.get("video_cache_total_mib"))
        overlay = settings.get("overlay_interaction", {}) or {}
        self._overlay_preferences = OverlayPreference.from_config(overlay)
        appearance = settings.get("appearance", {}) or {}
        self._graphics_acceleration = GraphicsAcceleration.from_config(appearance.get("graphics_acceleration", "auto"))
        logger.info(f"GlobalSettings loaded: overlays={OverlayPreference.config_value(self._overlay_preferences)}")

    def save_to_config(self, config_manager: ConfigManager, *, snapshot: dict[str, Any] | None = None) -> None:
        """Stage a complete supplied or current snapshot without emitting runtime signals."""
        values = self.snapshot() if snapshot is None else snapshot
        settings: dict[str, Any] = config_manager.get_raw("settings", {}) or {}
        ui = config_manager.get_raw("ui", {}) or {}
        ui["theme"] = values["appearance"]["theme"]
        window = ui.get("window", {}) or {}
        window["custom_frame"] = values["appearance"]["custom_frame"]
        ui["window"] = window
        config_manager.set("ui", ui)
        overlay = settings.get("overlay_interaction", {}) or {}
        overlay.update(values["overlay_interaction"])
        settings["overlay_interaction"] = overlay
        appearance = settings.get("appearance", {}) or {}
        appearance["graphics_acceleration"] = values["appearance"]["graphics_acceleration"]
        settings["appearance"] = appearance
        playback = settings.get("playback", {}) or {}
        playback["video_cache_total_mib"] = values["playback"]["video_cache_total_mib"]
        settings["playback"] = playback
        config_manager.set("settings", settings)
        logger.debug("GlobalSettings saved to config")

    def snapshot(self) -> dict[str, Any]:
        """Return a plain dict snapshot of all current settings."""
        return {
            "playback": {"video_cache_total_mib": self._video_cache_budget.config_value},
            "appearance": {
                "graphics_acceleration": self._graphics_acceleration.value,
                "theme": self._theme.value,
                "custom_frame": self._custom_frame,
            },
            "overlay_interaction": OverlayPreference.config_value(self._overlay_preferences),
        }

    def apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Apply a snapshot dict, emitting signals for changed values."""
        playback = snapshot.get("playback", {}) or {}
        if "video_cache_total_mib" in playback:
            self.video_cache_budget = VideoCacheBudget.from_config(playback["video_cache_total_mib"])
        overlay = snapshot.get("overlay_interaction", {}) or {}
        for preference in OverlayPreference:
            if preference.value in overlay:
                self.set_overlay_enabled(preference, bool(overlay[preference.value]))
        appearance = snapshot.get("appearance", {}) or {}
        if "theme" in appearance:
            self.theme = ThemeMode.from_config(appearance["theme"])
        if "custom_frame" in appearance:
            self.custom_frame = bool(appearance["custom_frame"])
        if "graphics_acceleration" in appearance:
            self.graphics_acceleration = GraphicsAcceleration.from_config(appearance["graphics_acceleration"])

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton — for testing only."""
        cls._instance = None
