"""Keeps the frame cache of offline loading tests in the test's own folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.cache.cache_manager import CacheManager


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: tmp_path / "cache")
