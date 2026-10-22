"""Validate the prepared app and its plugins before activating plugin changes.

Runs only as ``python -m`` inside the prepared plugin environment. Neither the launcher nor ``project.py`` may import
it, because it loads the application's plugin system (and Qt). On failure it writes the reason, naming the package, as
one ``FAILURE_PREFIX`` line of JSON on stdout, so other output cannot hide it, and exits 1; diagnostics and tracebacks
go to stderr.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path

from packaging.utils import canonicalize_name

from .host import current_host
from .project import FAILURE_PREFIX, read_project, selected_plugins


def validate_plugins(names: list[str]) -> None:
    """Reject missing, broken, incompatible, or shadowed selected plugins using the real application registry."""
    from ax_devil.modules.plugin_system.loader import ApplicationPluginLoader
    from ax_devil.modules.plugin_system.registry import PluginStatus, RuntimePluginRegistry

    ApplicationPluginLoader.load_all()
    groups = {family.entrypoint_group: family.plugin_type for family in ApplicationPluginLoader.PLUGIN_FAMILIES}
    for name in names:
        distribution = importlib.metadata.distribution(name)
        package = canonicalize_name(distribution.name)
        entries = [ep for ep in distribution.entry_points if ep.group in groups]
        if not entries:
            raise ValueError(f"{name} has no plugin entry points")
        for record in RuntimePluginRegistry.list_plugins():
            if record.package == package and record.status is PluginStatus.FAILED:
                raise ValueError(f"{package} cannot load: {record.error}")
        for entry in entries:
            plugin = entry.load()
            record = RuntimePluginRegistry.get_plugin(groups[entry.group], plugin.plugin_id())
            if (
                record.status is not PluginStatus.LOADED
                or record.plugin_class is not plugin
                or record.package != package
            ):
                raise ValueError(f"{name}:{entry.name} was rejected: {record.error or 'duplicate plugin ID'}")


def validate_installation(project: Path) -> None:
    """Reject an unexpected app build or copied/replaced editable packages, then validate the selected plugins."""
    metadata = read_project(project)
    host = current_host()
    sources = metadata["tool"]["uv"].get("sources", {})
    if host.fingerprint != metadata["tool"]["ax-devil"]["host"] or host.editable != ("ax-devil" in sources):
        raise ValueError("Prepared ax-devil does not match the base app")
    for name, source in sources.items():
        distribution = importlib.metadata.distribution(name)
        origin = json.loads(distribution.read_text("direct_url.json") or "{}")
        if not origin.get("dir_info", {}).get("editable") or origin.get("url") != Path(source["path"]).as_uri():
            raise ValueError(f"Prepared {name} does not use its selected editable source")

    validate_plugins(list(selected_plugins(project)))


if __name__ == "__main__":
    try:
        validate_installation(Path(sys.argv[1]))
    except Exception as exc:
        reason = f"{exc}" or type(exc).__name__
        sys.stdout.write(f"{FAILURE_PREFIX}{json.dumps(reason)}\n")
        sys.exit(1)
