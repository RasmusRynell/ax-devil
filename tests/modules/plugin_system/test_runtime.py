"""Installed plugins load through their entry points, and a broken one is recorded without stopping the rest."""

from __future__ import annotations

import importlib
from importlib import metadata
from logging import WARNING
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    ApplicationPluginLoader,
    DecoderPlugin,
    PlaylistResolverPlugin,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

DECODER_GROUP = "ax_devil.decoder_plugins"
RESOLVER_GROUP = "ax_devil.playlist_resolver_plugins"
PLUGIN_ID = "external-plugin"
_HOST_SCENE_MODEL = f"{SCENE_MODEL_VERSION[0]}.{SCENE_MODEL_VERSION[1]}"

pytestmark = pytest.mark.usefixtures("unloaded_plugins")


def _entry_point(plugin: Any, *, name: str = PLUGIN_ID, distribution: str = "example-distribution") -> SimpleNamespace:
    """Describe an installed entry point; ``plugin`` is what loading it returns, or an exception it raises."""

    def load() -> Any:
        if isinstance(plugin, Exception):
            raise plugin
        return plugin

    dist = SimpleNamespace(name=distribution, locate_file=lambda _: Path(f"/site-packages/{distribution}"))
    return SimpleNamespace(name=name, value=f"{name}:PLUGIN_CLASS", dist=dist, load=load)


def _load_installed(monkeypatch: pytest.MonkeyPatch, **groups: list[SimpleNamespace]) -> None:
    """Load all plugins with these entry points installed, keyed by ``decoders`` and ``resolvers``."""
    by_group = {DECODER_GROUP: groups.get("decoders", []), RESOLVER_GROUP: groups.get("resolvers", [])}
    monkeypatch.setattr(metadata, "entry_points", lambda *, group: by_group.get(group, []))
    ApplicationPluginLoader.load_all()


class ExternalDecoder(DecoderPlugin):
    """A valid external decoder."""

    @classmethod
    def required_api_version(cls) -> int:
        return 3

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
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
        return 3

    @classmethod
    def plugin_id(cls) -> str:
        return "external-resolver"

    @classmethod
    def display_name(cls) -> str:
        return "External Resolver"

    def resolve(self, settings: Any, token: CancelToken) -> Any:
        raise NotImplementedError

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


class PreviousApi(ExternalDecoder):
    @classmethod
    def required_api_version(cls) -> int:
        return 1


class WrongType(ExternalDecoder):
    @classmethod
    def plugin_type(cls) -> str:
        return "wrong-type"


class UndeclaredSceneModel(DecoderPlugin):
    @classmethod
    def required_api_version(cls) -> int:
        return 3

    @classmethod
    def plugin_id(cls) -> str:
        return PLUGIN_ID

    @classmethod
    def display_name(cls) -> str:
        return "Undeclared"


class PreviousSceneMajor(ExternalDecoder):
    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        return (1, 0)


class NewerSceneMajor(ExternalDecoder):
    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        return (SCENE_MODEL_VERSION[0] + 1, 0)


class NewerSceneMinor(ExternalDecoder):
    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
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
    assert decoder.package == "example-distribution"
    assert (
        RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "mot_challenge").status is PluginStatus.LOADED
    )


def test_a_distribution_with_unreadable_metadata_is_listed_as_failed_and_the_rest_still_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnreadableDistribution:
        @property
        def name(self) -> str:
            raise metadata.PackageNotFoundError("broken-distribution")

        def locate_file(self, _: str) -> Path:
            return Path("/site-packages/broken-distribution")

    broken = _entry_point(ExternalDecoder, name="broken-plugin")
    broken.dist = UnreadableDistribution()

    _load_installed(monkeypatch, decoders=[broken], resolvers=[_entry_point(ExternalResolver)])

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "broken-plugin")
    assert record.status is PluginStatus.FAILED
    assert record.error is not None and "broken-distribution" in record.error
    resolver = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "external-resolver")
    assert resolver.status is PluginStatus.LOADED


@pytest.mark.parametrize(
    ("plugin", "reason"),
    [
        pytest.param(
            ImportError("No module named 'missing'", name="missing"),
            "missing dependency: No module named 'missing'",
            id="import-error",
        ),
        pytest.param(
            ImportError("cannot import name 'Gone'", name="ax_devil.modules.workspace"),
            "incompatible with this ax-devil version: cannot import name 'Gone'",
            id="host-name-missing",
        ),
        pytest.param(object, "PLUGIN_CLASS missing or invalid", id="not-a-plugin"),
        pytest.param(UndeclaredApi, "does not implement required_api_version", id="undeclared-api"),
        pytest.param(BrokenApiDeclaration, "plugin validation failed: broken API declaration", id="raising-api"),
        pytest.param(FutureApi, "requires plugin API 999, host provides 3", id="future-api"),
        pytest.param(PreviousApi, "requires plugin API 1, host provides 3", id="previous-api"),
        pytest.param(WrongType, "expected 'decoder'", id="wrong-family"),
        pytest.param(UndeclaredSceneModel, "does not implement scene_model_version", id="no-scene-model"),
        pytest.param(
            PreviousSceneMajor,
            f"built for Scene model 1.0, this ax-devil uses Scene model {_HOST_SCENE_MODEL}; update the plugin",
            id="previous-scene-major",
        ),
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
    assert any(other.package is None and other.status is PluginStatus.LOADED for other in decoders)


@pytest.mark.parametrize(
    ("plugin", "missing"),
    [(UndeclaredApi, "required_api_version, scene_model_version"), (UndeclaredSceneModel, "scene_model_version")],
)
def test_a_missing_version_declaration_is_named_instead_of_reported_as_a_bad_version(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, missing: str
) -> None:
    _load_installed(monkeypatch, decoders=[_entry_point(plugin)])

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, PLUGIN_ID)
    assert (record.status, record.error) == (PluginStatus.FAILED, f"does not implement {missing}")


def test_resolver_without_headless_resolve_fails_to_load_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolver written for the widget-only contract is reported at startup instead of failing when used."""

    class WidgetOnlyResolver(PlaylistResolverPlugin):
        @classmethod
        def required_api_version(cls) -> int:
            return 3

        @classmethod
        def plugin_id(cls) -> str:
            return "widget-only"

        @classmethod
        def display_name(cls) -> str:
            return "Widget Only"

        def create_settings_widget(self) -> Any:
            raise NotImplementedError

    _load_installed(monkeypatch, resolvers=[_entry_point(WidgetOnlyResolver, name="widget-only")])

    record = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, "widget-only")
    assert record.status is PluginStatus.FAILED
    assert record.error == "does not implement resolve"


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


def test_a_builtin_that_fails_to_import_is_listed_as_failed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken built-in shows in the Plugins view like any other failed plugin, and the rest still load."""
    import_module = importlib.import_module

    def broken_mot(name: str) -> Any:
        if name.endswith("decoders.mot.plugin"):
            raise ImportError("mot is broken")
        return import_module(name)

    caplog.set_level(WARNING, logger="ax_devil.ax_devil.modules.plugin_system.loader")
    monkeypatch.setattr(importlib, "import_module", broken_mot)
    _load_installed(monkeypatch)

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, "mot")
    assert (record.status, record.package) == (PluginStatus.FAILED, None)
    assert record.error is not None and "mot is broken" in record.error
    assert any(r.status is PluginStatus.LOADED for r in RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE))
    summaries = [m for m in caplog.messages if "plugin(s) from" in m]
    assert len(summaries) == 1 and summaries[0].startswith("1 plugin(s) from built-in failed to load")


def test_a_package_whose_plugins_fail_is_warned_about_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(WARNING, logger="ax_devil.ax_devil.modules.plugin_system.loader")

    class FirstFuture(FutureApi):
        @classmethod
        def plugin_id(cls) -> str:
            return "first"

    class SecondFuture(FutureApi):
        @classmethod
        def plugin_id(cls) -> str:
            return "second"

    _load_installed(
        monkeypatch,
        decoders=[
            _entry_point(FirstFuture, name="first", distribution="suite"),
            _entry_point(SecondFuture, name="second", distribution="suite"),
        ],
    )

    warnings = [message for message in caplog.messages if "failed to load" in message]
    assert len(warnings) == 1 and "2 plugin(s) from suite" in warnings[0]


def test_a_package_is_recorded_by_its_normalized_distribution_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _load_installed(monkeypatch, decoders=[_entry_point(ExternalDecoder, distribution="Lab_Experiments")])

    assert RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, PLUGIN_ID).package == "lab-experiments"


def test_failures_sharing_a_plugin_id_are_all_kept(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(WARNING, logger="ax_devil.ax_devil.modules.plugin_system.loader")
    _load_installed(
        monkeypatch,
        decoders=[_entry_point(FutureApi, distribution="one"), _entry_point(FutureApi, distribution="two")],
    )

    failed = [r for r in RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE) if r.status is PluginStatus.FAILED]
    assert sorted(str(r.package) for r in failed) == ["one", "two"]
    assert len([m for m in caplog.messages if "failed to load" in m]) == 2


def test_valid_plugin_replaces_failed_same_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _load_installed(
        monkeypatch,
        decoders=[_entry_point(FutureApi, distribution="broken"), _entry_point(ExternalDecoder, distribution="valid")],
    )

    record = RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, PLUGIN_ID)
    assert record.status is PluginStatus.LOADED
    assert record.package == "valid"


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


def test_registry_scopes_unique_plugin_ids_by_plugin_type() -> None:
    class SameIdResolver(ExternalResolver):
        @classmethod
        def plugin_id(cls) -> str:
            return PLUGIN_ID

    RuntimePluginRegistry.reset()
    RuntimePluginRegistry.register_plugin(ExternalDecoder, "test")
    RuntimePluginRegistry.register_plugin(SameIdResolver, "test")

    assert RuntimePluginRegistry.get_plugin(DECODER_PLUGIN_TYPE, PLUGIN_ID).plugin_class is ExternalDecoder
    assert RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, PLUGIN_ID).plugin_class is SameIdResolver
    with pytest.raises(ValueError, match="Duplicate plugin id registered: external-plugin"):
        RuntimePluginRegistry.register_plugin(ExternalDecoder, "test")
