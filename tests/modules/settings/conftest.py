"""Isolate settings persistence from configuration selected by other test modules."""

from pathlib import Path

import pytest

from ax_devil.modules.settings.config_manager import ConfigManager


@pytest.fixture(autouse=True)
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """Give each settings test its own writable configuration document."""
    monkeypatch.setattr(ConfigManager, "_instance", None)
    config = ConfigManager()
    config.set_config_path(tmp_path / "config.json")
    config.set("storage", {key: str(tmp_path / key) for key in config.get_raw("storage")})
    return config
