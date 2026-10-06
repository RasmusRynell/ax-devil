"""Validate the prepared app and editable sources before activating plugin changes."""

from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger

from .host import current_host
from .project import read_project, selected_plugins

logger = get_logger(__name__)


def validate_installation(project: Path) -> None:
    """Reject an unexpected app build or copied/replaced editable packages."""
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

    from ax_devil.modules.plugin_system.validate import validate_plugins

    validate_plugins(list(selected_plugins(project)))


if __name__ == "__main__":
    try:
        validate_installation(Path(sys.argv[1]))
    except Exception as exc:
        logger.error(f"Installation validation failed: {exc}")
        sys.exit(1)
