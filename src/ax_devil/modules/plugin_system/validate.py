"""Check installed plugin entry points using the real application registry."""

from __future__ import annotations

import importlib.metadata

from .loader import ApplicationPluginLoader
from .registry import PluginStatus, RuntimePluginRegistry


def validate_plugins(names: list[str]) -> None:
    """Reject missing, broken, incompatible, or shadowed selected plugins."""
    ApplicationPluginLoader.load_all()
    groups = {family.entrypoint_group: family.plugin_type for family in ApplicationPluginLoader.PLUGIN_FAMILIES}
    for name in names:
        distribution = importlib.metadata.distribution(name)
        entries = [ep for ep in distribution.entry_points if ep.group in groups]
        if not entries:
            raise ValueError(f"{name} has no plugin entry points")
        for entry in entries:
            plugin = entry.load()
            record = RuntimePluginRegistry.get_plugin(groups[entry.group], plugin.plugin_id())
            if (
                record.status is not PluginStatus.LOADED
                or record.plugin_class is not plugin
                or record.origin != f"package:{distribution.name}"
            ):
                raise ValueError(f"{name}:{entry.name} was rejected: {record.error or 'duplicate plugin ID'}")
