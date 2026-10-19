"""Tests for GlobalSettings signals and its round trip through the configuration."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.playback_settings import VideoCacheBudget
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.settings.theme_mode import ThemeMode


class TestGlobalSettingsSignals:
    """Verify signal emission on property changes."""

    def test_overlay_preference_defaults_on_and_emits_signals_on_change_only(self) -> None:
        settings = GlobalSettings()
        preference_changes: list[tuple[OverlayPreference, bool]] = []
        setting_changes: list[tuple[str, Any]] = []
        settings.overlay_preference_changed.connect(lambda key, val: preference_changes.append((key, val)))
        settings.setting_changed.connect(lambda key, val: setting_changes.append((key, val)))

        assert all(settings.is_overlay_enabled(preference) for preference in OverlayPreference)
        settings.set_overlay_enabled(OverlayPreference.HOVER, True)
        assert preference_changes == []
        assert setting_changes == []

        settings.set_overlay_enabled(OverlayPreference.HOVER, False)

        assert preference_changes == [(OverlayPreference.HOVER, False)]
        assert setting_changes == [("overlay_interaction.hover_enabled", False)]


class TestGlobalSettingsConfigRoundTrip:
    """Verify load/save with ConfigManager."""

    def test_save_to_config_preserves_raw_settings(
        self, config: ConfigManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AX_DEVIL_TEST_SETTING", "resolved-value")
        config.set("settings", {"custom": {"keep": "$AX_DEVIL_TEST_SETTING"}})

        settings = GlobalSettings()
        settings.theme = ThemeMode.DARK
        settings.graphics_acceleration = GraphicsAcceleration.OFF
        settings.text_size = TextSize.LARGER
        settings.custom_frame = False
        settings.quick_setup_done = True
        assert settings.scan_on_startup and settings.scan_sign_in
        settings.scan_on_startup = False
        settings.scan_sign_in = False
        settings.video_cache_budget = VideoCacheBudget(512)
        for preference in OverlayPreference:
            settings.set_overlay_enabled(preference, False)
        expected = settings.snapshot()
        settings.save_to_config(config)
        config.save()

        saved = json.loads(config.config_path.read_text(encoding="utf-8"))
        assert saved["settings"]["custom"] == {"keep": "$AX_DEVIL_TEST_SETTING"}
        monkeypatch.setattr(ConfigManager, "_instance", None)
        reopened = ConfigManager()
        reopened.set_config_path(config.config_path, create_if_missing=False)
        GlobalSettings.reset_instance()
        restored = GlobalSettings()
        restored.load_from_config(reopened)
        assert restored.snapshot() == expected
        assert not any(restored.is_overlay_enabled(preference) for preference in OverlayPreference)
        assert reopened.get("settings")["custom"] == {"keep": "resolved-value"}


class TestGlobalSettingsSnapshot:
    """Verify snapshot / apply_snapshot round-trip."""

    def test_snapshot_round_trip_emits_on_change_only(self) -> None:
        settings = GlobalSettings()
        received: list[bool] = []
        settings.overlay_preference_changed.connect(lambda _preference, enabled: received.append(enabled))

        settings.set_overlay_enabled(OverlayPreference.HOVER, False)

        snap = settings.snapshot()

        settings.apply_snapshot(replace(settings.snapshot(), enabled_overlays=frozenset(OverlayPreference)))
        assert settings.is_overlay_enabled(OverlayPreference.HOVER) is True
        assert received == [False, True]

        settings.apply_snapshot(snap)
        assert settings.is_overlay_enabled(OverlayPreference.HOVER) is False
        assert received == [False, True, False]
        settings.apply_snapshot(snap)
        assert received == [False, True, False]


@pytest.mark.parametrize("invalid", ["unknown", {}])
def test_invalid_saved_appearance_follows_system(invalid: object) -> None:
    assert ThemeMode.from_config(invalid) is ThemeMode.AUTO
    assert TextSize.from_config(invalid) is TextSize.SYSTEM
