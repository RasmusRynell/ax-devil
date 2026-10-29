from __future__ import annotations

from pathlib import Path

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.overlay_persistence_views import OverlayPersistenceViews


def test_the_saved_default_survives_a_restart_and_the_built_in_default_is_stored_as_null(tmp_path: Path) -> None:
    ConfigManager().set_config_path(tmp_path / "c.json")
    custom = OverlayPersistenceSettings(enabled=True, timeout_ms=None, opacity=0.4)

    OverlayPersistenceViews().use_as_default(custom)
    ConfigManager().set_config_path(tmp_path / "other.json")
    ConfigManager().set_config_path(tmp_path / "c.json")

    assert OverlayPersistenceViews().default() == custom

    OverlayPersistenceViews().use_as_default(OverlayPersistenceSettings.default_enabled())

    ConfigManager().set_config_path(tmp_path / "other.json")
    ConfigManager().set_config_path(tmp_path / "c.json")

    assert ConfigManager().get("settings")["sticky_overlays"] is None
    assert OverlayPersistenceViews().default() == OverlayPersistenceSettings.default_enabled()

    for timeout_ms in (-1, 100001, 2**31, True):
        malformed = {"enabled": True, "timeout_ms": timeout_ms, "opacity": 0.4}
        assert OverlayPersistenceSettings.from_config(malformed) == OverlayPersistenceSettings.default_enabled()
