"""Installation validation must respect entry-point families and distribution ownership."""

from __future__ import annotations

from collections.abc import Generator
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ax_devil.modules.plugin_system import ApplicationPluginLoader, DecoderPlugin, RuntimePluginRegistry
from ax_devil.modules.plugin_system.validate import validate_plugins
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION


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
        return 2

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        """Declare the supported Scene model."""
        return SCENE_MODEL_VERSION


@pytest.fixture(autouse=True)
def isolated_loader() -> Generator[None, None, None]:
    """Load each scenario independently and restore built-ins after the test."""
    RuntimePluginRegistry.reset()
    ApplicationPluginLoader._loaded = False
    yield
    with patch.object(metadata, "entry_points", return_value=[]):
        ApplicationPluginLoader.reload_plugins()


@pytest.mark.parametrize("wrong_family,other_owner", [(False, False), (True, False), (False, True)])
def test_validate_entry_point_family_and_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrong_family: bool, other_owner: bool
) -> None:
    """A class registered elsewhere cannot validate a rejected entry point."""
    distribution = SimpleNamespace(name="example-distribution", locate_file=lambda _: tmp_path)
    groups = ["ax_devil.decoder_plugins"]
    if wrong_family:
        groups.append("ax_devil.playlist_resolver_plugins")
    entries = [
        SimpleNamespace(name="review-example", group=group, dist=distribution, load=lambda: ExampleDecoder)
        for group in groups
    ]
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
    monkeypatch.setattr(metadata, "distribution", lambda _: distribution)
    monkeypatch.setattr(metadata, "entry_points", lambda *, group: [ep for ep in visible_entries if ep.group == group])
    if wrong_family or other_owner:
        with pytest.raises(ValueError):
            validate_plugins([distribution.name])
    else:
        validate_plugins([distribution.name])
