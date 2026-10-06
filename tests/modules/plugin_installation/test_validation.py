"""Reject prepared environments that do not contain the requested app and sources."""

from __future__ import annotations

import importlib.metadata
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from ax_devil.modules.plugin_installation import project, validate
from ax_devil.modules.plugin_installation.host import current_host
from ax_devil.modules.plugin_system import validate as plugin_validation


@pytest.mark.parametrize("mismatch", [None, "app-version", "copied-app", "copied-plugin", "wrong-source"])
def test_prepared_sources_are_verified_before_plugin_imports(
    plugin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str | None
) -> None:
    """Matching plugin APIs cannot make an unexpected app or source eligible for activation."""
    host = current_host()
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    project._write_project(prepared, host, {"example-plugin": str(plugin)}, {})
    actual_host = host
    if mismatch == "app-version":
        actual_host = replace(host, version="9999")
    elif mismatch == "copied-app":
        actual_host = replace(host, editable=False)
    monkeypatch.setattr(validate, "current_host", lambda: actual_host)

    def distribution(name: str) -> Mock:
        source = host.location if name == "ax-devil" else plugin
        origin = {"url": source.as_uri(), "dir_info": {"editable": True}}
        if name == "example-plugin":
            if mismatch == "copied-plugin":
                origin["dir_info"] = {"editable": False}
            elif mismatch == "wrong-source":
                origin["url"] = tmp_path.as_uri()
        return Mock(read_text=Mock(return_value=json.dumps(origin)))

    monkeypatch.setattr(importlib.metadata, "distribution", distribution)
    load_plugins = Mock()
    monkeypatch.setattr(plugin_validation, "validate_plugins", load_plugins)
    if mismatch is None:
        validate.validate_installation(prepared)
        load_plugins.assert_called_once_with(["example-plugin"])
    else:
        with pytest.raises(ValueError, match="Prepared"):
            validate.validate_installation(prepared)
        load_plugins.assert_not_called()
