"""Tests for the generic runtime registry and decoder contracts."""

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
    ApplicationPluginLoader,
    DecoderPlugin,
    PlaylistResolverPlugin,
    PluginBase,
    PluginDefinitionBase,
    PluginFamily,
    RuntimePluginRegistry,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

DECODER_FAMILY = PluginFamily(
    plugin_type=DECODER_PLUGIN_TYPE,
    plugin_base_class=DecoderPlugin,
    builtin_root=Path("/nonexistent"),
    entrypoint_group="test.decoder_plugins",
)


@pytest.fixture(autouse=True)
def _restore_plugins() -> Generator[None, None, None]:
    """Isolate each registry test and restore built-ins afterward."""
    RuntimePluginRegistry.reset()
    yield
    with patch("ax_devil.modules.plugin_system.loader.importlib.metadata.entry_points", return_value=[]):
        ApplicationPluginLoader.reload_plugins()


class ExampleFamilyPlugin(PluginBase):
    """Plugin fixture for a non-built-in family."""

    @classmethod
    def required_api_version(cls) -> int:
        return 1

    @classmethod
    def plugin_type(cls) -> str:
        return "example"

    @classmethod
    def plugin_id(cls) -> str:
        return "example-plugin"

    @classmethod
    def display_name(cls) -> str:
        return "Example Plugin"


class WrongTypePlugin(DecoderPlugin):
    """Decoder fixture that declares an incompatible family."""

    @classmethod
    def required_api_version(cls) -> int:
        return 1

    @classmethod
    def plugin_type(cls) -> str:
        return "wrong-type"

    @classmethod
    def plugin_id(cls) -> str:
        return "wrong-type-plugin"

    @classmethod
    def display_name(cls) -> str:
        return "Wrong Type Plugin"


class IncompatiblePlugin(DecoderPlugin):
    """Decoder fixture requiring a future plugin API."""

    @classmethod
    def required_api_version(cls) -> int:
        return 999

    @classmethod
    def plugin_id(cls) -> str:
        return "incompatible-plugin"

    @classmethod
    def display_name(cls) -> str:
        return "Incompatible Plugin"


def test_application_loader_loads_installed_entry_point(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Installed package entry points should register plugin classes automatically."""

    class ExamplePlugin(DecoderPlugin):
        @classmethod
        def required_api_version(cls) -> int:
            return 1

        @classmethod
        def scene_model_version(cls) -> tuple[int, int]:
            return SCENE_MODEL_VERSION

        @classmethod
        def plugin_id(cls) -> str:
            return "installed-example"

        @classmethod
        def display_name(cls) -> str:
            return "Installed Example"

    distribution = SimpleNamespace(name="example-distribution", locate_file=lambda _: tmp_path)
    entrypoint = SimpleNamespace(
        name="installed-example",
        value="example_plugin:PLUGIN_CLASS",
        dist=distribution,
        load=lambda: ExamplePlugin,
    )
    monkeypatch.setattr(metadata, "entry_points", lambda **_: [entrypoint])

    family = DECODER_FAMILY
    ApplicationPluginLoader._load_family_from_entrypoints(family)

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "installed-example")
    assert record.definition.display_name == "Installed Example"
    assert record.entrypoint == tmp_path
    assert record.origin == "package:example-distribution"


def test_application_loader_records_entrypoint_metadata_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken distribution metadata lookup should not abort plugin discovery."""

    entrypoint = SimpleNamespace(name="metadata-error", load=lambda: object)
    monkeypatch.setattr(metadata, "entry_points", lambda **_: [entrypoint])

    def fail_path(_: Any) -> Path:
        raise RuntimeError("broken distribution metadata")

    monkeypatch.setattr(ApplicationPluginLoader, "_entrypoint_path", staticmethod(fail_path))
    family = DECODER_FAMILY

    ApplicationPluginLoader._load_family_from_entrypoints(family)

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "metadata-error")
    assert record.status.value == "failed"
    assert record.error == "entry-point discovery failed: broken distribution metadata"


def test_runtime_registry_rejects_duplicate_plugin_ids(tmp_path: Path) -> None:
    """The generic runtime registry should reject duplicate plugin identifiers."""

    definition = PluginDefinitionBase(plugin_type="example", plugin_id="duplicate", display_name="Duplicate")
    RuntimePluginRegistry.register_definition(definition, tmp_path / "one.py", "test")

    with pytest.raises(ValueError, match="Duplicate plugin id registered: duplicate"):
        RuntimePluginRegistry.register_definition(definition, tmp_path / "two.py", "test")


def test_runtime_registry_allows_same_plugin_id_for_different_plugin_types(tmp_path: Path) -> None:
    """The generic registry should scope plugin ids by plugin_type."""

    definition_a = PluginDefinitionBase(plugin_type="type-a", plugin_id="shared", display_name="Shared A")
    definition_b = PluginDefinitionBase(plugin_type="type-b", plugin_id="shared", display_name="Shared B")

    RuntimePluginRegistry.register_definition(definition_a, tmp_path / "a.py", "test")
    RuntimePluginRegistry.register_definition(definition_b, tmp_path / "b.py", "test")

    assert RuntimePluginRegistry.get_plugin_ids("type-a") == ["shared"]
    assert RuntimePluginRegistry.get_plugin_ids("type-b") == ["shared"]
    assert RuntimePluginRegistry.get_plugin("type-a", "shared").definition.display_name == "Shared A"
    assert RuntimePluginRegistry.get_plugin("type-b", "shared").definition.display_name == "Shared B"


def test_application_loader_marks_invalid_plugin_class_as_failed(tmp_path: Path) -> None:
    """Invalid PLUGIN_CLASS exports should be recorded as failed."""
    family = DECODER_FAMILY
    ApplicationPluginLoader._register_plugin_class(
        family,
        object,
        tmp_path,
        "package:broken",
        plugin_id_hint="broken",
    )

    records = RuntimePluginRegistry.list_plugins()
    assert len(records) == 1
    assert records[0].definition.plugin_id == "broken"
    assert records[0].definition.plugin_type == DECODER_PLUGIN_TYPE
    assert records[0].status.value == "failed"
    assert records[0].error == "PLUGIN_CLASS missing or invalid"


def test_application_loader_marks_plugin_validation_exception_as_failed(tmp_path: Path) -> None:
    """A plugin validation exception should not abort application startup."""

    class BrokenPlugin(DecoderPlugin):
        @classmethod
        def required_api_version(cls) -> int:
            raise RuntimeError("broken API declaration")

        @classmethod
        def plugin_id(cls) -> str:
            return "broken-plugin"

        @classmethod
        def display_name(cls) -> str:
            return "Broken Plugin"

    family = DECODER_FAMILY
    ApplicationPluginLoader._register_plugin_class(
        family,
        BrokenPlugin,
        tmp_path,
        "package:broken",
        plugin_id_hint="broken-plugin",
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "broken-plugin")
    assert record.status.value == "failed"
    assert record.error == "plugin validation failed: broken API declaration"


def test_external_plugin_must_declare_api_version(tmp_path: Path) -> None:
    """External plugins must opt into the host API explicitly."""

    class UndeclaredPlugin(DecoderPlugin):
        @classmethod
        def plugin_id(cls) -> str:
            return "undeclared-plugin"

        @classmethod
        def display_name(cls) -> str:
            return "Undeclared Plugin"

    family = DECODER_FAMILY
    ApplicationPluginLoader._register_plugin_class(
        family,
        UndeclaredPlugin,
        tmp_path,
        "package:undeclared",
        plugin_id_hint="undeclared-plugin",
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "undeclared-plugin")
    assert record.status.value == "failed"
    assert record.error == "must explicitly declare required_api_version"


@pytest.mark.parametrize(
    ("version", "error"),
    [
        (None, "must explicitly declare scene_model_version"),
        (
            (SCENE_MODEL_VERSION[0] + 1, 0),
            f"built for Scene model {SCENE_MODEL_VERSION[0] + 1}.0, this ax-devil uses Scene model "
            f"{SCENE_MODEL_VERSION[0]}.{SCENE_MODEL_VERSION[1]}; update the plugin",
        ),
        (
            (SCENE_MODEL_VERSION[0], SCENE_MODEL_VERSION[1] + 1),
            f"built for Scene model {SCENE_MODEL_VERSION[0]}.{SCENE_MODEL_VERSION[1] + 1}, this ax-devil uses "
            f"Scene model {SCENE_MODEL_VERSION[0]}.{SCENE_MODEL_VERSION[1]}; update the plugin",
        ),
    ],
)
def test_decoder_plugin_rejects_incompatible_scene_model(
    tmp_path: Path, version: tuple[int, int] | None, error: str
) -> None:
    """Decoder plugins must declare a Scene model version the host can read."""

    class ScenePlugin(DecoderPlugin):
        @classmethod
        def required_api_version(cls) -> int:
            return 1

        @classmethod
        def scene_model_version(cls) -> tuple[int, int] | None:
            return version

        @classmethod
        def plugin_id(cls) -> str:
            return "scene-plugin"

        @classmethod
        def display_name(cls) -> str:
            return "Scene Plugin"

    ApplicationPluginLoader._register_plugin_class(
        DECODER_FAMILY, ScenePlugin, tmp_path, "package:scene", plugin_id_hint="scene-plugin"
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "scene-plugin")
    assert record.status.value == "failed"
    assert record.error == error


def test_registration_failure_keeps_the_plugin_id(tmp_path: Path) -> None:
    """Registry failures retain the plugin ID even when the entry point has a different name."""
    family = PluginFamily("example", PluginBase, tmp_path, "test.example_plugins")
    with patch.object(RuntimePluginRegistry, "register_plugin", side_effect=RuntimeError("registration failed")):
        ApplicationPluginLoader._register_plugin_class(
            family, ExampleFamilyPlugin, tmp_path, "package:example", plugin_id_hint="different-entry-point"
        )
    record = RuntimePluginRegistry.get_plugin("example", "example-plugin")
    assert record.status.value == "failed"
    assert record.error == "registration failed"


def test_decoder_plugin_uses_decoder_plugin_type() -> None:
    """Decoder plugins should register under the shared decoder plugin type."""

    class ExampleDecoderPlugin(DecoderPlugin):
        @classmethod
        def plugin_id(cls) -> str:
            return "example-decoder"

        @classmethod
        def display_name(cls) -> str:
            return "Example Decoder"

    assert ExampleDecoderPlugin.definition().plugin_type == DECODER_PLUGIN_TYPE


def test_application_loader_supports_another_plugin_family(tmp_path: Path) -> None:
    """A second plugin family should load without runtime changes."""
    family = PluginFamily(
        plugin_type="example",
        plugin_base_class=PluginBase,
        builtin_root=Path("/nonexistent"),
        entrypoint_group="test.example_plugins",
    )
    ApplicationPluginLoader._register_plugin_class(
        family,
        ExampleFamilyPlugin,
        tmp_path,
        "package:example",
        plugin_id_hint="example-plugin",
    )

    record = RuntimePluginRegistry.get_plugin("example", "example-plugin")
    assert record.definition.display_name == "Example Plugin"


def test_application_loader_rejects_plugin_type_mismatch(tmp_path: Path) -> None:
    """A family loader should reject plugins declaring the wrong plugin_type."""
    family = DECODER_FAMILY
    ApplicationPluginLoader._register_plugin_class(
        family,
        WrongTypePlugin,
        tmp_path,
        "package:wrong-type",
        plugin_id_hint="wrong-type-plugin",
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "wrong-type-plugin")
    assert record.status.value == "failed"
    assert record.error == "PLUGIN_CLASS declared plugin_type='wrong-type', expected 'decoder'"


def test_application_loader_rejects_incompatible_plugin_api(tmp_path: Path) -> None:
    """Plugins requiring another API version should fail before registration."""
    family = DECODER_FAMILY
    ApplicationPluginLoader._register_plugin_class(
        family,
        IncompatiblePlugin,
        tmp_path,
        "package:incompatible",
        plugin_id_hint="incompatible-plugin",
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "incompatible-plugin")
    assert record.status.value == "failed"
    assert record.error == "requires plugin API 999, host provides 1"


def test_valid_plugin_replaces_failed_same_id(tmp_path: Path) -> None:
    """A later valid plugin should recover an earlier failed record with the same ID."""

    class ValidPlugin(DecoderPlugin):
        @classmethod
        def required_api_version(cls) -> int:
            return 1

        @classmethod
        def scene_model_version(cls) -> tuple[int, int]:
            return SCENE_MODEL_VERSION

        @classmethod
        def plugin_id(cls) -> str:
            return "incompatible-plugin"

        @classmethod
        def display_name(cls) -> str:
            return "Valid Plugin"

    family = DECODER_FAMILY
    ApplicationPluginLoader._register_plugin_class(
        family,
        IncompatiblePlugin,
        tmp_path / "broken",
        "package:broken",
        plugin_id_hint="incompatible-plugin",
    )
    ApplicationPluginLoader._register_plugin_class(
        family,
        ValidPlugin,
        tmp_path / "valid",
        "package:valid",
        plugin_id_hint="incompatible-plugin",
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "incompatible-plugin")
    assert record.status.value == "loaded"
    assert record.definition.display_name == "Valid Plugin"


def test_duplicate_plugin_id_does_not_replace_loaded_plugin(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A duplicate plugin id must not hide an already loaded plugin."""
    caplog.set_level(WARNING, logger="ax_devil.ax_devil.modules.plugin_system.loader")

    class FirstPlugin(DecoderPlugin):
        @classmethod
        def required_api_version(cls) -> int:
            return 1

        @classmethod
        def scene_model_version(cls) -> tuple[int, int]:
            return SCENE_MODEL_VERSION

        @classmethod
        def plugin_id(cls) -> str:
            return "shared-plugin"

        @classmethod
        def display_name(cls) -> str:
            return "First Plugin"

    class SecondPlugin(FirstPlugin):
        @classmethod
        def display_name(cls) -> str:
            return "Second Plugin"

    family = DECODER_FAMILY
    ApplicationPluginLoader._register_plugin_class(
        family, FirstPlugin, tmp_path / "first", "package:first", plugin_id_hint="shared-plugin"
    )
    ApplicationPluginLoader._register_plugin_class(
        family, SecondPlugin, tmp_path / "second", "package:second", plugin_id_hint="shared-plugin"
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "shared-plugin")
    assert record.status.value == "loaded"
    assert record.definition.display_name == "First Plugin"
    assert any("Duplicate plugin id" in message for message in caplog.messages)


def test_runtime_registry_stores_plugin_class(tmp_path: Path) -> None:
    """Loaded plugin records should retain the plugin class."""

    class ExampleResolver(PlaylistResolverPlugin):
        @classmethod
        def plugin_id(cls) -> str:
            return "example-resolver"

        @classmethod
        def display_name(cls) -> str:
            return "Example Resolver"

        def create_settings_widget(self) -> Any:
            raise NotImplementedError

    record = RuntimePluginRegistry.register_plugin(ExampleResolver, tmp_path / "plugin.py", "test")
    assert record.plugin_class is ExampleResolver
