"""Where the UI screenshots go and how their offscreen screen is scaled, set before Qt starts."""

from __future__ import annotations

import atexit
import json
import math
import os
import tempfile
from pathlib import Path

import pytest

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize

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
# themselves to the screen, so the offscreen screen keeps its default 800x800 logical size at every scale.
os.environ["QT_SCALE_FACTOR"] = str(SCALE)
# Qt multiplies these into the scale, or turns it off, when the calling shell sets them.
for _inherited in ("QT_SCREEN_SCALE_FACTORS", "QT_AUTO_SCREEN_SCALE_FACTOR", "QT_ENABLE_HIGHDPI_SCALING"):
    os.environ.pop(_inherited, None)
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    # One file per run, so overlapping runs with different scales never read each other's screen.
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="ax-devil-ui-shots-", delete=False) as config:
        json.dump({"screens": [{"name": "shots", "width": round(800 * SCALE), "height": round(800 * SCALE)}]}, config)
    _SCREEN_CONFIG = Path(config.name)
    atexit.register(_SCREEN_CONFIG.unlink, missing_ok=True)
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={_SCREEN_CONFIG}"
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
