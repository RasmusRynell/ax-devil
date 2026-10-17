"""Generic plugin registry and metadata store."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from ax_devil.modules.settings.logging_config import get_logger

from .base import PluginBase, PluginDefinitionBase

logger = get_logger(__name__)


_BUILT_IN_SOURCE = "built-in"


class PluginStatus(Enum):
    """Lifecycle stages for a plugin bundle."""

    LOADED = "loaded"
    FAILED = "failed"


@dataclass
class PluginRecord:
    """Metadata captured for each discovered plugin."""

    definition: PluginDefinitionBase
    package: str | None
    status: PluginStatus
    plugin_class: type[PluginBase] | None = None
    error: str | None = None

    @property
    def source(self) -> str:
        """Return what to call the plugin's source in messages: its package, or "built-in"."""
        return self.package or _BUILT_IN_SOURCE


class RuntimePluginRegistry:
    """Generic registry that only tracks plugin definitions."""

    _plugins_by_type: dict[str, dict[str, PluginRecord]] = {}

    @classmethod
    def reset(cls) -> None:
        """Clear all registered plugins."""
        cls._plugins_by_type.clear()

    @classmethod
    def register_plugin(
        cls,
        plugin_cls: type[PluginBase],
        package: str | None,
    ) -> PluginRecord:
        """Register a plugin class under its definition's type and id."""
        definition = plugin_cls.definition()
        plugins = cls._plugins_by_type.setdefault(definition.plugin_type, {})
        existing = plugins.get(definition.plugin_id)
        if existing is not None and existing.status == PluginStatus.LOADED:
            raise ValueError(f"Duplicate plugin id registered: {definition.plugin_id}")
        if existing is not None:
            logger.info(f"Replacing failed plugin record: {definition.plugin_type}:{definition.plugin_id}")

        record = PluginRecord(
            definition=definition,
            package=package,
            status=PluginStatus.LOADED,
            plugin_class=plugin_cls,
        )
        plugins[definition.plugin_id] = record
        return record

    @classmethod
    def mark_failed(
        cls,
        plugin_type: str,
        plugin_id: str,
        package: str | None,
        error_message: str,
    ) -> None:
        """Record a failed plugin import.

        A failure whose id is already taken by a failure from another source is kept under ``id (source)``, so every
        broken package stays listed. *package* is the normalized distribution name, or None for a built-in plugin.
        """
        plugins = cls._plugins_by_type.setdefault(plugin_type, {})
        existing = plugins.get(plugin_id)
        if existing is not None and existing.status is PluginStatus.FAILED and existing.package != package:
            plugin_id = f"{plugin_id} ({package or _BUILT_IN_SOURCE})"
            existing = plugins.get(plugin_id)
        if existing is not None:
            logger.warning(f"Refusing to overwrite existing plugin record for failed plugin {plugin_type}:{plugin_id}")
            return

        definition = PluginDefinitionBase(
            plugin_type=plugin_type,
            plugin_id=plugin_id,
            display_name=plugin_id,
            description=None,
        )
        plugins[plugin_id] = PluginRecord(
            definition=definition,
            package=package,
            status=PluginStatus.FAILED,
            error=error_message,
        )

    @classmethod
    def get_plugin(cls, plugin_type: str, plugin_id: str) -> PluginRecord:
        """Return a single plugin record for the requested type and id."""
        plugins = cls._plugins_by_type.get(plugin_type, {})
        record = plugins.get(plugin_id)
        if record is None:
            raise ValueError(f"Unknown plugin: {plugin_type}:{plugin_id}")
        return record

    @classmethod
    def get_plugins(cls, plugin_type: str) -> list[PluginRecord]:
        """Return all plugin records for the requested type."""
        return list(cls._plugins_by_type.get(plugin_type, {}).values())

    @classmethod
    def list_plugins(cls) -> list[PluginRecord]:
        """Return all discovered plugin records."""
        plugins: Iterable[dict[str, PluginRecord]] = cls._plugins_by_type.values()
        return [record for plugin_group in plugins for record in plugin_group.values()]


__all__ = ["PluginRecord", "PluginStatus", "RuntimePluginRegistry"]
