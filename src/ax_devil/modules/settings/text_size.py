"""Text size preference for the whole application."""

from __future__ import annotations

from enum import Enum

from ax_devil.modules.chrome.tokens import DEFAULT_BODY_PX, system_body_px
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class TextSize(str, Enum):
    """Saved body text size; every text style and row height scales from it."""

    SYSTEM = "system"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    LARGER = "larger"

    @property
    def body_px(self) -> int:
        """Return the body text size in pixels; **System** reads the operating system's interface text size."""
        if self is TextSize.SYSTEM:
            return system_body_px()
        return {
            self.SMALL: DEFAULT_BODY_PX - 2,
            self.MEDIUM: DEFAULT_BODY_PX,
            self.LARGE: DEFAULT_BODY_PX + 3,
            self.LARGER: DEFAULT_BODY_PX + 6,
        }[self]

    @classmethod
    def largest_px(cls) -> int:
        """Return the largest body text size any choice gives on this system."""
        return max(size.body_px for size in cls)

    @property
    def label(self) -> str:
        """Return the settings label."""
        return self.name.title()

    @classmethod
    def from_config(cls, value: object) -> TextSize:
        """Read a saved preference, following the system's text size for invalid values."""
        try:
            return cls(value)
        except (ValueError, TypeError):
            logger.warning(f"Invalid text size setting {value!r}; following the system's text size.")
            return cls.SYSTEM
