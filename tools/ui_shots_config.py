"""Where the UI screenshots go and how their private display is scaled, set before Qt starts."""

from __future__ import annotations

import atexit
import math
import os
from pathlib import Path

import pytest

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from tools import private_display

OUT = Path(os.environ.get("AX_DEVIL_UI_SHOTS", "/tmp/ax-devil-ui-shots"))


def _shot_scale() -> float:
    """Return the device pixels per logical pixel chosen with ``AX_DEVIL_UI_SHOTS_SCALE``, 2 by default."""
    value = os.environ.get("AX_DEVIL_UI_SHOTS_SCALE", "2")
    try:
        scale = float(value)
    except ValueError:
        scale = math.nan
    if not (math.isfinite(scale) and scale > 0):
        raise pytest.UsageError(f"AX_DEVIL_UI_SHOTS_SCALE must be a positive number such as 1 or 2, not {value!r}")
    return scale


SCALE = _shot_scale()
# Qt reads these when the application starts, which pytest-qt does after collecting this module. Dialogs fit
# themselves to the screen, so the private display keeps an 800x800 logical size at every scale.
os.environ["QT_SCALE_FACTOR"] = str(SCALE)
# Qt multiplies these into the scale, or turns it off, when the calling shell sets them.
for _inherited in ("QT_SCREEN_SCALE_FACTORS", "QT_AUTO_SCREEN_SCALE_FACTOR", "QT_ENABLE_HIGHDPI_SCALING"):
    os.environ.pop(_inherited, None)
# One display per run, so overlapping runs with different scales never share a screen. It replaces the one the test
# package started, which is 1920x1080.
_DISPLAY = private_display.start(round(800 * SCALE), round(800 * SCALE))
atexit.register(private_display.stop, _DISPLAY)
os.environ["DISPLAY"] = _DISPLAY.name
WINDOW_SIZES = {"wide": (1440, 900), "narrow": (1000, 640)}
GRID_SIZE = (1920, 1080)
TEXT_SIZES = (TextSize.MEDIUM, TextSize.LARGER)


def configured(theme: str) -> ConfigManager:
    """Save a configuration with *theme* and no start-up dialogs or network scan, and return it."""
    config = ConfigManager()
    config.set("ui", {"theme": theme, "text_size": "medium", "quick_setup_done": True})
    config.set("settings", {"network_scan": {"on_startup": False, "sign_in": False}})
    config.save()
    GlobalSettings.reset_instance()
    return config
