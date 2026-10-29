from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ax_devil.modules.filtering.filter_choice import FilterChoice
from ax_devil.modules.filtering.measure_range import Measure, MeasureRange
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.view_settings import (
    OBJECT_FILTER,
    OVERLAY_SYNC,
    STICKY_OVERLAYS,
    SettingKind,
    ViewSettings,
)


def _restart(config_path: Path) -> None:
    ConfigManager().set_config_path(config_path.with_name("other.json"))
    ConfigManager().set_config_path(config_path)


@pytest.mark.parametrize(
    ("kind", "custom"),
    [
        (STICKY_OVERLAYS, OverlayPersistenceSettings(enabled=True, timeout_ms=None, opacity=0.4)),
        (OVERLAY_SYNC, TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY)),
        (
            OBJECT_FILTER,
            FilterChoice(
                classes=frozenset({("show_cars", False)}),
                ranges=frozenset({(Measure.SCORE, MeasureRange(minimum=0.5))}),
            ),
        ),
    ],
)
def test_the_saved_default_survives_a_restart_and_the_built_in_value_is_stored_as_null(
    tmp_path: Path, kind: SettingKind[Any], custom: object
) -> None:
    config_path = tmp_path / "c.json"
    ConfigManager().set_config_path(config_path)

    ViewSettings(kind).use_as_default(custom)
    _restart(config_path)

    assert ViewSettings(kind).default() == custom

    ViewSettings(kind).use_as_default(kind.built_in)
    _restart(config_path)

    assert ConfigManager().get("settings")["view_defaults"][kind.key] is None
    assert ViewSettings(kind).default() == kind.built_in


@pytest.mark.parametrize(
    ("kind", "malformed"),
    [
        (STICKY_OVERLAYS, {"enabled": True, "timeout_ms": timeout_ms, "opacity": 0.4})
        for timeout_ms in (-1, 100001, True)
    ]
    + [(OVERLAY_SYNC, {"mode": "bogus"}), (OBJECT_FILTER, {"classes": {}, "ranges": {"Speed": {}}})],
)
def test_a_malformed_saved_default_is_replaced_by_the_built_in_value(
    tmp_path: Path, kind: SettingKind[Any], malformed: object
) -> None:
    ConfigManager().set_config_path(tmp_path / "c.json")
    ConfigManager().get_raw("settings")["view_defaults"][kind.key] = malformed

    assert ViewSettings(kind).default() == kind.built_in
