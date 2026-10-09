"""Global application settings with reactive change propagation.

Provides a singleton ``GlobalSettings`` QObject that sits between
``ConfigManager`` (file I/O) and the UI.  Settings are mutable at runtime
and emit Qt signals on change so consumers can react without polling.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.playback_settings import VideoCacheBudget, detect_available_memory_bytes
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.settings.theme_mode import ThemeMode

logger = get_logger(__name__)


@dataclass(frozen=True)
class SettingsState:
    """Complete immutable runtime preferences, independent of the persisted document shape."""

    video_cache_budget: VideoCacheBudget = VideoCacheBudget()
    enabled_overlays: frozenset[OverlayPreference] = frozenset(OverlayPreference)
    graphics_acceleration: GraphicsAcceleration = GraphicsAcceleration.AUTO
    theme: ThemeMode = ThemeMode.AUTO
    text_size: TextSize = TextSize.SYSTEM
    custom_frame: bool = True
    quick_setup_done: bool = False


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
    text_size_changed = Signal(object)  # TextSize

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
        self._state = SettingsState()
        # Capture the inherited override before startup applies our own presentation default.
        self.graphics_acceleration_override = os.environ.get("QT_WIDGETS_RHI")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def video_cache_budget(self) -> VideoCacheBudget:
        """Return the total memory preference shared by all offline sources."""
        return self._state.video_cache_budget

    @video_cache_budget.setter
    def video_cache_budget(self, value: VideoCacheBudget) -> None:
        if value == self._state.video_cache_budget:
            return
        self._state = replace(self._state, video_cache_budget=value)
        self.video_cache_budget_changed.emit(value)
        self.setting_changed.emit("playback.video_cache_total_mib", value.config_value)

    def is_overlay_enabled(self, preference: OverlayPreference) -> bool:
        """Return whether viewers show *preference*."""
        return preference in self._state.enabled_overlays

    def set_overlay_enabled(self, preference: OverlayPreference, enabled: bool) -> None:
        """Turn *preference* on or off for every viewer."""
        if enabled == self.is_overlay_enabled(preference):
            return
        overlays = self._state.enabled_overlays
        self._state = replace(
            self._state, enabled_overlays=overlays | {preference} if enabled else overlays - {preference}
        )
        logger.debug(f"{preference.value} changed to {enabled}")
        self.overlay_preference_changed.emit(preference, enabled)
        self.setting_changed.emit(f"overlay_interaction.{preference.value}", enabled)

    @property
    def custom_frame(self) -> bool:
        """Whether to use the application's title bar after the next restart."""
        return self._state.custom_frame

    @custom_frame.setter
    def custom_frame(self, value: bool) -> None:
        if value == self._state.custom_frame:
            return
        self._state = replace(self._state, custom_frame=value)
        self.setting_changed.emit("appearance.custom_frame", value)

    @property
    def theme(self) -> ThemeMode:
        """Return the application appearance preference."""
        return self._state.theme

    @theme.setter
    def theme(self, value: ThemeMode) -> None:
        if value == self._state.theme:
            return
        self._state = replace(self._state, theme=value)
        self.theme_changed.emit(value.value)
        self.setting_changed.emit("appearance.theme", value.value)

    @property
    def text_size(self) -> TextSize:
        """Return the body text size preference."""
        return self._state.text_size

    @text_size.setter
    def text_size(self, value: TextSize) -> None:
        if value == self._state.text_size:
            return
        self._state = replace(self._state, text_size=value)
        self.text_size_changed.emit(value)
        self.setting_changed.emit("appearance.text_size", value.value)

    @property
    def quick_setup_done(self) -> bool:
        """Return whether Quick Setup has been closed with this configuration; until then it opens on start."""
        return self._state.quick_setup_done

    @quick_setup_done.setter
    def quick_setup_done(self, value: bool) -> None:
        if value == self._state.quick_setup_done:
            return
        self._state = replace(self._state, quick_setup_done=value)
        self.setting_changed.emit("quick_setup_done", value)

    @property
    def graphics_acceleration(self) -> GraphicsAcceleration:
        """Return the saved presentation preference, applied on the next startup."""
        return self._state.graphics_acceleration

    @graphics_acceleration.setter
    def graphics_acceleration(self, value: GraphicsAcceleration) -> None:
        if value == self._state.graphics_acceleration:
            return
        self._state = replace(self._state, graphics_acceleration=value)
        self.setting_changed.emit("appearance.graphics_acceleration", value.value)

    # ------------------------------------------------------------------
    # Config round-trip
    # ------------------------------------------------------------------

    def load_from_config(self, config_manager: ConfigManager) -> None:
        """Read settings from ``ConfigManager`` without emitting signals."""
        settings: dict[str, Any] = config_manager.get("settings", {}) or {}
        ui = config_manager.get("ui", {}) or {}
        playback = settings.get("playback", {}) or {}
        overlay = settings.get("overlay_interaction", {}) or {}
        appearance = settings.get("appearance", {}) or {}
        self._state = SettingsState(
            theme=ThemeMode.from_config(ui.get("theme", "auto")),
            text_size=TextSize.from_config(ui.get("text_size", TextSize.SYSTEM.value)),
            custom_frame=bool((ui.get("window", {}) or {}).get("custom_frame", True)),
            quick_setup_done=bool(ui.get("quick_setup_done", False)),
            video_cache_budget=VideoCacheBudget.from_config(playback.get("video_cache_total_mib")),
            enabled_overlays=OverlayPreference.from_config(overlay),
            graphics_acceleration=GraphicsAcceleration.from_config(appearance.get("graphics_acceleration", "auto")),
        )
        logger.debug(f"GlobalSettings loaded: overlays={self._state.enabled_overlays}")

    def save_to_config(self, config_manager: ConfigManager, *, snapshot: SettingsState | None = None) -> None:
        """Stage a complete supplied or current snapshot without emitting runtime signals."""
        values = self.snapshot() if snapshot is None else snapshot
        settings: dict[str, Any] = config_manager.get_raw("settings", {}) or {}
        ui = config_manager.get_raw("ui", {}) or {}
        ui["theme"] = values.theme.value
        ui["text_size"] = values.text_size.value
        ui["quick_setup_done"] = values.quick_setup_done
        window = ui.get("window", {}) or {}
        window["custom_frame"] = values.custom_frame
        ui["window"] = window
        config_manager.set("ui", ui)
        overlay = settings.get("overlay_interaction", {}) or {}
        overlay.update(OverlayPreference.config_value(values.enabled_overlays))
        settings["overlay_interaction"] = overlay
        appearance = settings.get("appearance", {}) or {}
        appearance["graphics_acceleration"] = values.graphics_acceleration.value
        settings["appearance"] = appearance
        playback = settings.get("playback", {}) or {}
        playback["video_cache_total_mib"] = values.video_cache_budget.config_value
        settings["playback"] = playback
        config_manager.set("settings", settings)
        logger.debug("GlobalSettings saved to config")

    def snapshot(self) -> SettingsState:
        """Return the current complete immutable runtime value."""
        return self._state

    def apply_snapshot(self, snapshot: SettingsState) -> None:
        """Apply complete typed preferences, emitting signals only for changed values."""
        self.video_cache_budget = snapshot.video_cache_budget
        for preference in OverlayPreference:
            self.set_overlay_enabled(preference, preference in snapshot.enabled_overlays)
        self.theme = snapshot.theme
        self.text_size = snapshot.text_size
        self.custom_frame = snapshot.custom_frame
        self.graphics_acceleration = snapshot.graphics_acceleration
        self.quick_setup_done = snapshot.quick_setup_done

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton — for testing only."""
        cls._instance = None
