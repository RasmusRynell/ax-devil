"""Application appearance choices."""

from __future__ import annotations

from enum import Enum

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class ThemeMode(str, Enum):
    """Saved light/dark preference, including following the operating system."""

    AUTO = "auto"
    LIGHT = "light"
    DARK = "dark"

    @property
    def label(self) -> str:
        """Return the settings label."""
        return {self.AUTO: "System", self.LIGHT: "Light", self.DARK: "Dark"}[self]

    @classmethod
    def from_config(cls, value: object) -> ThemeMode:
        """Read a saved preference, using the system appearance for invalid values."""
        try:
            return cls(value)
        except (ValueError, TypeError):
            logger.warning(f"Invalid theme setting {value!r}; following system appearance.")
            return cls.AUTO
