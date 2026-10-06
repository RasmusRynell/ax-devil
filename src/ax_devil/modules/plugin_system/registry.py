"""Generic plugin registry and metadata store."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from ax_devil.modules.settings.logging_config import get_logger

from .base import PluginBase, PluginDefinitionBase

logger = get_logger(__name__)


class PluginStatus(Enum):
    """Lifecycle stages for a plugin bundle."""

    LOADED = "loaded"
    FAILED = "failed"


@dataclass
class PluginRecord:
    """Metadata captured for each discovered plugin."""

    definition: PluginDefinitionBase
    entrypoint: Path
    origin: str
    status: PluginStatus
    plugin_class: type[PluginBase] | None = None
    error: str | None = None


class RuntimePluginRegistry:
    """Generic registry that only tracks plugin definitions."""

    _plugins_by_type: dict[str, dict[str, PluginRecord]] = {}

    @classmethod
    def reset(cls) -> None:
        """Clear all registered plugins."""
        cls._plugins_by_type.clear()

    @classmethod
    def register_definition(
        cls,
        definition: PluginDefinitionBase,
        entrypoint: Path,
        origin: str,
        plugin_class: type[PluginBase] | None = None,
    ) -> PluginRecord:
        """Register a concrete plugin definition."""
        plugins = cls._plugins_by_type.setdefault(definition.plugin_type, {})
        existing = plugins.get(definition.plugin_id)
        if existing is not None and existing.status == PluginStatus.LOADED:
            raise ValueError(f"Duplicate plugin id registered: {definition.plugin_id}")
        if existing is not None:
            logger.info(f"Replacing failed plugin record: {definition.plugin_type}:{definition.plugin_id}")

        record = PluginRecord(
            definition=definition,
            entrypoint=entrypoint,
            origin=origin,
            status=PluginStatus.LOADED,
            plugin_class=plugin_class,
        )
        plugins[definition.plugin_id] = record
        return record

    @classmethod
    def register_plugin(
        cls,
        plugin_cls: type[PluginBase],
        entrypoint: Path,
        origin: str,
    ) -> PluginRecord:
        """Register a plugin class by resolving its definition."""
        return cls.register_definition(plugin_cls.definition(), entrypoint, origin, plugin_class=plugin_cls)

    @classmethod
    def mark_failed(
        cls,
        plugin_type: str,
        plugin_id: str,
        entrypoint: Path,
        origin: str,
        error_message: str,
    ) -> None:
        """Record a failed plugin import."""
        plugins = cls._plugins_by_type.setdefault(plugin_type, {})
        if plugin_id in plugins:
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
            entrypoint=entrypoint,
            origin=origin,
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
    def get_plugin_ids(cls, plugin_type: str) -> list[str]:
        """Return all plugin ids for the requested type."""
        return list(cls._plugins_by_type.get(plugin_type, {}).keys())

    @classmethod
    def list_plugins(cls) -> list[PluginRecord]:
        """Return all discovered plugin records."""
        plugins: Iterable[dict[str, PluginRecord]] = cls._plugins_by_type.values()
        return [record for plugin_group in plugins for record in plugin_group.values()]


__all__ = ["PluginRecord", "PluginStatus", "RuntimePluginRegistry"]
