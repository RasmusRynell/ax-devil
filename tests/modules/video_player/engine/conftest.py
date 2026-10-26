"""Real Quick surfaces for the native backend tests."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from tests.helpers.quick_surface import open_surface


@pytest.fixture
def surface(qtbot: QtBot) -> QuickSurface:
    """Create a real scene graph surface (software in ordinary offscreen CI)."""
    return open_surface(qtbot)


@pytest.fixture
def other_surface(qtbot: QtBot) -> QuickSurface:
    """Retain the second viewer through QtBot's native widget teardown."""
    return open_surface(qtbot)
