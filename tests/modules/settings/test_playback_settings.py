"""Shared playback memory preferences resolve Auto and resize live sources."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import (
    FrameCache,
    get_frame_cache_registry,
)
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.video_cache_memory import get_video_cache_pool
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.playback_settings import VideoCacheBudget, detect_available_memory_bytes
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.settings_dialog import SettingsDialog
from tests.helpers.video import create_test_video


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Use a predictable 8 GiB of available RAM and isolate the preference."""
    monkeypatch.setattr("ax_devil.modules.settings.settings.detect_available_memory_bytes", lambda: 8 * 1024**3)
    GlobalSettings.reset_instance()
    yield
    GlobalSettings.reset_instance()


@pytest.mark.parametrize("invalid", [None, True, "512", 256.5, 0, -1, 1048577, {}, []])
def test_invalid_saved_budget_uses_auto(invalid: object) -> None:
    assert VideoCacheBudget.from_config(invalid).mib is None


@pytest.mark.parametrize(
    ("ram_bytes", "expected_bytes"),
    [(16 * 1024**3, 4 * 1024**3), (3 * 1024**3, 768 * 1024**2), (1024**3, 256 * 1024**2), (0, 0), (None, 1024**3)],
)
def test_auto_uses_a_quarter_of_available_ram_without_a_fixed_ceiling(
    ram_bytes: int | None, expected_bytes: int
) -> None:
    assert VideoCacheBudget.from_config("auto").resolve_bytes(ram_bytes) == expected_bytes
    assert VideoCacheBudget(2048).resolve_bytes(ram_bytes) == 2 * 1024**3


def test_available_memory_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    meminfo = "MemTotal: 16777216 kB\nMemFree: 2097152 kB\nMemAvailable: 8388608 kB\n"
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: meminfo)
    assert detect_available_memory_bytes() == 8 * 1024**3

    def unavailable(*args: object, **kwargs: object) -> str:
        raise OSError("Memory information unavailable")

    monkeypatch.setattr(Path, "read_text", unavailable)
    assert detect_available_memory_bytes() is None


@pytest.mark.parametrize("meminfo", ["MemTotal: 16777216 kB", "MemAvailable: invalid kB", "MemAvailable:"])
def test_missing_or_invalid_available_memory_is_unknown(meminfo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: meminfo)
    assert detect_available_memory_bytes() is None


@pytest.mark.parametrize("value", [512, "auto"])
def test_budget_persistence_and_change_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: int | str
) -> None:
    monkeypatch.setattr(ConfigManager, "_instance", None)
    config = ConfigManager()
    config.set_config_path(tmp_path / "settings.json")
    settings = GlobalSettings()
    settings.video_cache_budget = VideoCacheBudget(1024)
    changes: list[tuple[str, object]] = []
    settings.setting_changed.connect(lambda key, value: changes.append((key, value)))
    settings.apply_snapshot({"playback": {"video_cache_total_mib": value}})
    settings.apply_snapshot({"playback": {"video_cache_total_mib": value}})
    assert changes == [("playback.video_cache_total_mib", value)]
    settings.save_to_config(config)
    config.save()
    monkeypatch.setattr(ConfigManager, "_instance", None)
    restored_config = ConfigManager()
    restored_config.set_config_path(tmp_path / "settings.json", create_if_missing=False)
    GlobalSettings.reset_instance()
    GlobalSettings().load_from_config(restored_config)
    assert GlobalSettings().video_cache_budget.config_value == value


def test_dialog_cancel_apply_and_return_to_auto(qtbot: QtBot) -> None:
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    assert dialog._video_cache_mode.currentData() is True
    assert not dialog._video_cache_spin.isEnabled()
    dialog._video_cache_mode.setCurrentIndex(1)
    dialog._video_cache_spin.setValue(1.5)
    assert dialog._video_cache_spin.isEnabled()
    dialog.reject()
    assert GlobalSettings().video_cache_budget.mib is None

    applied = SettingsDialog()
    qtbot.addWidget(applied)
    applied._video_cache_mode.setCurrentIndex(1)
    applied._video_cache_spin.setValue(1.5)
    applied._on_apply()
    applied._video_cache_spin.setValue(2.0)
    applied.reject()
    assert GlobalSettings().video_cache_budget.mib == 1536

    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    assert reopened._video_cache_mode.currentData() is False
    assert reopened._video_cache_spin.value() == 1.5
    reopened._video_cache_mode.setCurrentIndex(0)
    assert reopened._video_cache_mode.currentData() is True
    assert GlobalSettings().video_cache_budget.mib == 1536
    reopened._on_ok()
    assert GlobalSettings().video_cache_budget.mib is None


def test_pool_created_on_loader_thread_receives_live_changes_without_an_event_loop(qtbot: QtBot) -> None:
    settings = GlobalSettings()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pool = executor.submit(get_video_cache_pool).result(timeout=5)
    cache = FrameCache(0, pool=pool)
    try:
        assert cache.budget_bytes == 2 * 1024**3
        settings.video_cache_budget = VideoCacheBudget(512)
        assert cache.budget_bytes == 512 * 1024**2
    finally:
        cache.close()


def test_open_close_and_settings_changes_resize_existing_sources(tmp_path: Path, qtbot: QtBot) -> None:
    path = tmp_path / "source.mp4"
    create_test_video(path)
    settings = GlobalSettings()
    settings.video_cache_budget = VideoCacheBudget(512)
    pool = get_video_cache_pool()
    first = FileFrameSource(str(path), source_id="budget-first")

    def budget(label: str) -> int:
        return next(
            entry["budget_bytes"]
            for entry in get_frame_cache_registry().snapshot_details().values()
            if entry["label"] == label
        )

    try:
        assert first.read_decoded_frame(0) is not None
        assert budget("budget-first") == 512 * 1024**2
        second = FileFrameSource(str(path), source_id="budget-second")
        try:
            assert second.read_decoded_frame(0) is not None
            assert pool.source_count == 2
            assert budget("budget-first") == budget("budget-second") == 256 * 1024**2
            settings.video_cache_budget = VideoCacheBudget(256)
            assert budget("budget-first") == budget("budget-second") == 128 * 1024**2
            settings.video_cache_budget = VideoCacheBudget()
            assert budget("budget-first") == budget("budget-second") == 1024**3
            with pytest.raises(ValueError):
                FileFrameSource(str(tmp_path / "missing.mp4"))
            assert pool.source_count == 2
            with patch(
                "ax_devil.modules.data_sources.file_frame_source.VideoAnalyzer.analyze",
                side_effect=ValueError("Failed after registering cache"),
            ):
                with pytest.raises(ValueError, match="Failed after registering cache"):
                    FileFrameSource(str(path), source_id="failed-open")
            assert pool.source_count == 2
            assert budget("budget-first") == budget("budget-second") == 1024**3
        finally:
            second.stop()
            assert second.wait()
            second.deleteLater()
        assert pool.source_count == 1
        assert budget("budget-first") == 2 * 1024**3
        assert first.read_decoded_frame(10) is not None
    finally:
        first.stop()
        assert first.wait()
        first.deleteLater()
    assert pool.source_count == 0
