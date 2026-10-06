"""Graphics presentation choices and the application's automatic policy."""

from __future__ import annotations

import os
from enum import Enum

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class GraphicsAcceleration(str, Enum):
    """Restart-required viewer rendering and widget presentation preference."""

    AUTO = "auto"
    OFF = "off"

    @property
    def label(self) -> str:
        """Return the display label for this choice."""
        return self.value.capitalize()

    @property
    def description(self) -> str:
        """Describe the presentation policy without promising driver detection."""
        return {
            self.AUTO: "Prepare accelerated presentation at startup. Qt chooses the graphics backend.",
            self.OFF: "Use Qt Quick software rendering for graphics troubleshooting.",
        }[self]

    @classmethod
    def from_config(cls, value: object) -> GraphicsAcceleration:
        """Read a saved choice, falling back to Auto for invalid configuration."""
        try:
            return cls(value)
        except (ValueError, TypeError):
            logger.warning(f"Invalid graphics acceleration setting {value!r}; using auto.")
            return cls.AUTO

    def configure(self) -> None:
        """Configure Qt before creating widget windows, preserving environment overrides."""
        if self is self.OFF:
            os.environ.setdefault("QT_QUICK_BACKEND", "software")
        os.environ.setdefault("QT_WIDGETS_RHI", "0" if self is self.OFF else "1")
