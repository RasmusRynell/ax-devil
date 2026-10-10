"""Installed plugins load through their entry points, and a broken one is recorded without stopping the rest."""

from __future__ import annotations

from collections.abc import Generator
from importlib import metadata
from logging import WARNING
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    ApplicationPluginLoader,
    DecoderPlugin,
    PlaylistResolverPlugin,
    PluginDefinitionBase,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

DECODER_GROUP = "ax_devil.decoder_plugins"
RESOLVER_GROUP = "ax_devil.playlist_resolver_plugins"
PLUGIN_ID = "external-plugin"


@pytest.fixture(autouse=True)
def _restore_plugins() -> Generator[None, None, None]:
    """Restore the built-in plugins after each test."""
    yield
    with patch("ax_devil.modules.plugin_system.loader.importlib.metadata.entry_points", return_value=[]):
        ApplicationPluginLoader.reload_plugins()


def _entry_point(plugin: Any, *, name: str = PLUGIN_ID, distribution: str = "example-distribution") -> SimpleNamespace:
    """Describe an installed entry point; ``plugin`` is what loading it returns, or an exception it raises."""

    def load() -> Any:
        if isinstance(plugin, Exception):
            raise plugin
        return plugin

    dist = SimpleNamespace(name=distribution, locate_file=lambda _: Path(f"/site-packages/{distribution}"))
    return SimpleNamespace(name=name, value=f"{name}:PLUGIN_CLASS", dist=dist, load=load)


def _load_installed(monkeypatch: pytest.MonkeyPatch, **groups: list[SimpleNamespace]) -> None:
    """Reload all plugins with these entry points installed, keyed by ``decoders`` and ``resolvers``."""
    by_group = {DECODER_GROUP: groups.get("decoders", []), RESOLVER_GROUP: groups.get("resolvers", [])}
    monkeypatch.setattr(metadata, "entry_points", lambda *, group: by_group.get(group, []))
    ApplicationPluginLoader.reload_plugins()


class ExternalDecoder(DecoderPlugin):
    """A valid external decoder."""

    @classmethod
    def required_api_version(cls) -> int:
        return 1

    @classmethod
    def scene_model_version(cls) -> tuple[int, int] | None:
        return SCENE_MODEL_VERSION

    @classmethod
    def plugin_id(cls) -> str:
        return PLUGIN_ID

    @classmethod
    def display_name(cls) -> str:
        return "External Decoder"


class ExternalResolver(PlaylistResolverPlugin):
    """A valid external playlist resolver."""

    @classmethod
    def required_api_version(cls) -> int:
        return 1

    @classmethod
    def plugin_id(cls) -> str:
        return "external-resolver"

    @classmethod
    def display_name(cls) -> str:
        return "External Resolver"

    def create_settings_widget(self) -> Any:
        raise NotImplementedError


class BrokenApiDeclaration(ExternalDecoder):
    @classmethod
    def required_api_version(cls) -> int:
        raise RuntimeError("broken API declaration")


class FutureApi(ExternalDecoder):
    @classmethod
    def required_api_version(cls) -> int:
        return 999


class WrongType(ExternalDecoder):
    @classmethod
    def plugin_type(cls) -> str:
        return "wrong-type"


class UndeclaredSceneModel(ExternalDecoder):
    @classmethod
    def scene_model_version(cls) -> tuple[int, int] | None:
        return None


class NewerSceneMajor(ExternalDecoder):
    @classmethod
    def scene_model_version(cls) -> tuple[int, int] | None:
        return (SCENE_MODEL_VERSION[0] + 1, 0)


class NewerSceneMinor(ExternalDecoder):
    @classmethod
    def scene_model_version(cls) -> tuple[int, int] | None:
        return (SCENE_MODEL_VERSION[0], SCENE_MODEL_VERSION[1] + 1)


class UndeclaredApi(DecoderPlugin):
    @classmethod
    def plugin_id(cls) -> str:
        return PLUGIN_ID

    @classmethod
    def display_name(cls) -> str:
        return "Undeclared"


def test_installed_entry_points_register_beside_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    _load_installed(monkeypatch, decoders=[_entry_point(ExternalDecoder)], resolvers=[_entry_point(ExternalResolver)])

    decoder = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, PLUGIN_ID)
    resolver = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "external-resolver")
    assert (decoder.status, decoder.plugin_class) == (PluginStatus.LOADED, ExternalDecoder)
    assert (resolver.status, resolver.plugin_class) == (PluginStatus.LOADED, ExternalResolver)
    assert decoder.origin == "package:example-distribution"
    assert decoder.entrypoint == Path("/site-packages/example-distribution")
    assert (
        RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "mot_challenge").status is PluginStatus.LOADED
    )


@pytest.mark.parametrize(
    ("plugin", "reason"),
    [
        pytest.param(ImportError("No module named 'missing'"), "entry-point discovery failed", id="import-error"),
        pytest.param(object, "PLUGIN_CLASS missing or invalid", id="not-a-plugin"),
        pytest.param(UndeclaredApi, "must explicitly declare required_api_version", id="undeclared-api"),
        pytest.param(BrokenApiDeclaration, "plugin validation failed: broken API declaration", id="raising-api"),
        pytest.param(FutureApi, "requires plugin API 999, host provides 1", id="future-api"),
        pytest.param(WrongType, "expected 'decoder'", id="wrong-family"),
        pytest.param(UndeclaredSceneModel, "must explicitly declare scene_model_version", id="no-scene-model"),
        pytest.param(NewerSceneMajor, f"built for Scene model {SCENE_MODEL_VERSION[0] + 1}.0", id="scene-major"),
        pytest.param(
            NewerSceneMinor, f"built for Scene model {SCENE_MODEL_VERSION[0]}.{SCENE_MODEL_VERSION[1] + 1}", id="minor"
        ),
    ],
)
def test_rejected_plugin_is_recorded_with_its_reason(monkeypatch: pytest.MonkeyPatch, plugin: Any, reason: str) -> None:
    """The plugin list explains why an installed plugin is unavailable, and every other plugin still loads."""
    _load_installed(monkeypatch, decoders=[_entry_point(plugin)], resolvers=[_entry_point(ExternalResolver)])

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, PLUGIN_ID)
    assert record.status is PluginStatus.FAILED
    assert record.error is not None and reason in record.error
    resolver = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "external-resolver")
    assert resolver.status is PluginStatus.LOADED
    decoders = RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE)
    assert any(other.origin == "builtin" and other.status is PluginStatus.LOADED for other in decoders)


def test_registration_failure_is_recorded_under_the_plugin_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plugin is listed by its own ID, even when its entry point has another name."""

    class BrokenDescription(ExternalResolver):
        @classmethod
        def description(cls) -> str | None:
            raise RuntimeError("registration failed")

    _load_installed(monkeypatch, resolvers=[_entry_point(BrokenDescription, name="different-entry-point")])

    record = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "external-resolver")
    assert record.status is PluginStatus.FAILED
    assert record.error == "registration failed"


def test_valid_plugin_replaces_failed_same_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _load_installed(
        monkeypatch,
        decoders=[_entry_point(FutureApi, distribution="broken"), _entry_point(ExternalDecoder, distribution="valid")],
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, PLUGIN_ID)
    assert record.status is PluginStatus.LOADED
    assert record.origin == "package:valid"


def test_duplicate_plugin_id_does_not_replace_loaded_plugin(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(WARNING, logger="ax_devil.ax_devil.modules.plugin_system.loader")

    class SecondDecoder(ExternalDecoder):
        pass

    _load_installed(
        monkeypatch,
        decoders=[
            _entry_point(ExternalDecoder, distribution="first"),
            _entry_point(SecondDecoder, distribution="second"),
        ],
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, PLUGIN_ID)
    assert (record.status, record.plugin_class) == (PluginStatus.LOADED, ExternalDecoder)
    assert any("Duplicate plugin id" in message for message in caplog.messages)


def test_registry_scopes_unique_plugin_ids_by_plugin_type(tmp_path: Path) -> None:
    RuntimePluginRegistry.reset()
    for plugin_type in ("type-a", "type-b"):
        definition = PluginDefinitionBase(plugin_type=plugin_type, plugin_id="shared", display_name=plugin_type)
        RuntimePluginRegistry.register_definition(definition, tmp_path / f"{plugin_type}.py", "test")

    assert RuntimePluginRegistry.get_plugin("type-a", "shared").definition.display_name == "type-a"
    assert RuntimePluginRegistry.get_plugin("type-b", "shared").definition.display_name == "type-b"
    duplicate = PluginDefinitionBase(plugin_type="type-a", plugin_id="shared", display_name="again")
    with pytest.raises(ValueError, match="Duplicate plugin id registered: shared"):
        RuntimePluginRegistry.register_definition(duplicate, tmp_path / "again.py", "test")
