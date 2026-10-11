"""Generic plugin contract used by the runtime layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import RuntimePluginRegistry

PLUGIN_API_VERSION = 2


@dataclass(frozen=True)
class PluginDefinitionBase:
    """Minimal metadata common to all plugin types."""

    plugin_type: str
    plugin_id: str
    display_name: str
    description: str | None = None


class PluginBase(ABC):
    """Generic plugin base class used by the runtime layer."""

    @classmethod
    @abstractmethod
    def plugin_type(cls) -> str:
        """Return the plugin category used by the generic runtime."""

    @classmethod
    @abstractmethod
    def plugin_id(cls) -> str:
        """Return a unique identifier for the plugin."""

    @classmethod
    @abstractmethod
    def display_name(cls) -> str:
        """Return the human-readable plugin name."""

    @classmethod
    def description(cls) -> str | None:
        """Return an optional human-readable description."""
        return None

    @classmethod
    def required_api_version(cls) -> int:
        """Return the ax-devil plugin API version required by this plugin."""
        return PLUGIN_API_VERSION

    @classmethod
    def definition(cls) -> PluginDefinitionBase:
        """Return the plugin definition consumed by the runtime registry."""
        return PluginDefinitionBase(
            plugin_type=cls.plugin_type(),
            plugin_id=cls.plugin_id(),
            display_name=cls.display_name(),
            description=cls.description(),
        )

    @classmethod
    def validate_registration(cls, registry: type[RuntimePluginRegistry]) -> str | None:
        """Run plugin-type-specific validation before registration.

        Return an error message string to reject the plugin, or None to accept.
        Subclasses override this to enforce domain constraints.
        """
        return None


__all__ = ["PLUGIN_API_VERSION", "PluginBase", "PluginDefinitionBase"]
