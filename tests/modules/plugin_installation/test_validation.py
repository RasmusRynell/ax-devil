"""Reject prepared environments without the requested app and sources, and plugins the application rejects."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ax_devil.modules.plugin_installation import project, validate
from ax_devil.modules.plugin_installation.host import current_host
from ax_devil.modules.plugin_system import DecoderPlugin
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION


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
    monkeypatch.setattr(validate, "validate_plugins", load_plugins)
    if mismatch is None:
        validate.validate_installation(prepared)
        load_plugins.assert_called_once_with(["example-plugin"])
    else:
        with pytest.raises(ValueError, match="Prepared"):
            validate.validate_installation(prepared)
        load_plugins.assert_not_called()


class ExampleDecoder(DecoderPlugin):
    """A valid decoder that can be exported by several entry points."""

    @classmethod
    def plugin_id(cls) -> str:
        """Return the plugin's stable identifier."""
        return "review-example"

    @classmethod
    def display_name(cls) -> str:
        """Return the plugin's label."""
        return "Review example"

    @classmethod
    def required_api_version(cls) -> int:
        """Declare the supported host API."""
        return 3

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        """Declare the supported Scene model."""
        return SCENE_MODEL_VERSION


@pytest.mark.usefixtures("unloaded_plugins")
@pytest.mark.parametrize(
    "wrong_family,other_owner,old_scene_model,missing_dependency",
    [
        (False, False, False, False),
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (False, False, False, True),
    ],
)
def test_validate_entry_point_family_and_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrong_family: bool,
    other_owner: bool,
    old_scene_model: bool,
    missing_dependency: bool,
) -> None:
    """Reject wrong-family, shadowed or incompatible plugins before activating an installation."""
    if old_scene_model:
        monkeypatch.setattr(ExampleDecoder, "scene_model_version", classmethod(lambda cls: (1, 0)))
    distribution = SimpleNamespace(name="example-distribution", locate_file=lambda _: tmp_path)
    groups = ["ax_devil.decoder_plugins"]
    if wrong_family:
        groups.append("ax_devil.playlist_resolver_plugins")

    def load() -> type[ExampleDecoder]:
        if missing_dependency:
            raise ModuleNotFoundError("No module named 'cv3'", name="cv3")
        return ExampleDecoder

    entries = [SimpleNamespace(name="review-example", group=group, dist=distribution, load=load) for group in groups]
    distribution.entry_points = entries
    visible_entries = list(entries)
    if other_owner:
        visible_entries.insert(
            0,
            SimpleNamespace(
                name="review-example",
                group=groups[0],
                dist=SimpleNamespace(name="other-distribution", locate_file=lambda _: tmp_path),
                load=lambda: ExampleDecoder,
            ),
        )
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _: distribution)
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda *, group: [ep for ep in visible_entries if ep.group == group]
    )
    if missing_dependency:
        with pytest.raises(ValueError, match="example-distribution cannot load: missing dependency"):
            validate.validate_plugins([distribution.name])
    elif wrong_family or other_owner or old_scene_model:
        reason = "built for Scene model 1.0, this ax-devil uses Scene model 2.0" if old_scene_model else None
        with pytest.raises(ValueError, match=reason):
            validate.validate_plugins([distribution.name])
    else:
        validate.validate_plugins([distribution.name])


def test_failure_reason_reaches_the_result_file_despite_plugin_output_and_exit_hooks(tmp_path: Path) -> None:
    """A plugin's stdout and exit hooks cannot hide or replace why validation failed."""
    result = tmp_path / "reason.txt"
    script = """
import atexit, os, runpy, sys
from ax_devil.modules.plugin_installation import project

def unreadable(_):
    sys.stdout.write("Loading backend...")  # Unterminated, and lost when the exit hook skips flushing.
    raise ImportError("Binary ABI mismatch\\nReinstall the backend")

project.read_project = unreadable
atexit.register(os._exit, 18)
runpy.run_module("ax_devil.modules.plugin_installation.validate", run_name="__main__")
"""
    command = [sys.executable, "-c", script, str(tmp_path), str(result)]
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    completed = subprocess.run(command, capture_output=True, text=True, env=environment)
    assert completed.returncode != 0
    assert result.read_text(encoding="utf-8") == "Binary ABI mismatch\nReinstall the backend"
