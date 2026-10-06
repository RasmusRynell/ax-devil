"""Ax-devil decoder plugin contract and definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence, TypeAlias, cast

from ax_devil.modules.data_sources.file_data_provider.base import FileDataProviderFactory
from ax_devil.modules.filtering import FilterFactory
from ax_devil.modules.scene.decoding import PayloadToSceneDecoderFactory
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

from .base import PluginBase, PluginDefinitionBase

if TYPE_CHECKING:
    from .registry import RuntimePluginRegistry

DECODER_PLUGIN_TYPE = "decoder"

HandlerTypeName: TypeAlias = str


@dataclass(frozen=True)
class FileToSceneDecoderDefinition:
    """Description of a file-to-scene decoder contributed by a decoder plugin."""

    handler_type: HandlerTypeName
    factory: FileDataProviderFactory
    display_name: str
    filter_factory: FilterFactory | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    file_extensions: tuple[str, ...] = ()
    """Lowercase file suffixes such as ``".txt"`` this decoder reads; empty means it may read any file."""


@dataclass(frozen=True)
class PayloadToSceneDecoderDefinition:
    """Description of a payload decoder contributed by a decoder plugin."""

    handler_type: HandlerTypeName
    decoder_factory: PayloadToSceneDecoderFactory
    display_name: str
    filter_factory: FilterFactory | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecoderPluginDefinition(PluginDefinitionBase):
    """Structured definition returned by every decoder plugin."""

    file_to_scene_decoders: tuple[FileToSceneDecoderDefinition, ...] = ()
    payload_to_scene_decoders: tuple[PayloadToSceneDecoderDefinition, ...] = ()


class DecoderPlugin(PluginBase):
    """Abstract base class implemented by every decoder plugin bundle."""

    @classmethod
    def plugin_type(cls) -> str:
        """Return the generic plugin category for decoder bundles."""
        return DECODER_PLUGIN_TYPE

    @classmethod
    def file_to_scene_decoders(cls) -> Sequence[FileToSceneDecoderDefinition]:
        """Return file-to-scene decoders contributed by this plugin."""
        return ()

    @classmethod
    def scene_model_version(cls) -> tuple[int, int] | None:
        """Return the ``(major, minor)`` Scene model version this plugin builds Scenes for."""
        return None

    @classmethod
    def payload_to_scene_decoders(cls) -> Sequence[PayloadToSceneDecoderDefinition]:
        """Return payload decoder definitions contributed by this plugin."""
        return ()

    @classmethod
    def definition(cls) -> DecoderPluginDefinition:
        """Bundle decoder metadata into a single definition object."""
        return DecoderPluginDefinition(
            plugin_type=cls.plugin_type(),
            plugin_id=cls.plugin_id(),
            display_name=cls.display_name(),
            description=cls.description(),
            file_to_scene_decoders=tuple(cls.file_to_scene_decoders()),
            payload_to_scene_decoders=tuple(cls.payload_to_scene_decoders()),
        )

    @classmethod
    def validate_registration(cls, registry: type[RuntimePluginRegistry]) -> str | None:
        """Reject incompatible Scene model versions and duplicate file/payload handler types."""
        version = cls.scene_model_version()
        if version is None:
            return "must explicitly declare scene_model_version"
        host_major, host_minor = SCENE_MODEL_VERSION
        if version[0] != host_major or version[1] > host_minor:
            return (
                f"built for Scene model {version[0]}.{version[1]}, this ax-devil uses Scene model "
                f"{host_major}.{host_minor}; update the plugin"
            )
        candidate = cls.definition()
        for record in registry.get_plugins(DECODER_PLUGIN_TYPE):
            if record.status.value != "loaded":
                continue
            existing = cast(DecoderPluginDefinition, record.definition)
            error = cls._find_duplicate_handler_error(candidate, existing)
            if error is not None:
                return error
        return None

    @staticmethod
    def _find_duplicate_handler_error(
        candidate: DecoderPluginDefinition,
        existing: DecoderPluginDefinition,
    ) -> str | None:
        existing_file_handlers = {h.handler_type for h in existing.file_to_scene_decoders}
        for handler in candidate.file_to_scene_decoders:
            if handler.handler_type in existing_file_handlers:
                return (
                    f"duplicate file decoder handler {handler.handler_type!r} already registered by "
                    f"{existing.plugin_id!r}"
                )

        existing_payload_handlers = {h.handler_type for h in existing.payload_to_scene_decoders}
        for payload_handler in candidate.payload_to_scene_decoders:
            if payload_handler.handler_type in existing_payload_handlers:
                return (
                    f"duplicate payload decoder handler {payload_handler.handler_type!r} already registered by "
                    f"{existing.plugin_id!r}"
                )

        return None


# ---------------------------------------------------------------------------
# Convenience helpers for querying loaded file-to-scene decoders
# ---------------------------------------------------------------------------


def get_file_decoder_definitions() -> list[FileToSceneDecoderDefinition]:
    """Return all file-to-scene decoder definitions from loaded plugins."""
    from .registry import PluginStatus, RuntimePluginRegistry

    results: list[FileToSceneDecoderDefinition] = []
    for record in RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE):
        if record.status != PluginStatus.LOADED:
            continue
        defn = record.definition
        if isinstance(defn, DecoderPluginDefinition):
            results.extend(defn.file_to_scene_decoders)
    return results


def get_file_decoder_factory(handler_type: str) -> FileDataProviderFactory:
    """Look up the file-to-scene decoder factory for *handler_type*.

    Raises ``ValueError`` when no loaded plugin provides that handler type.
    """
    for defn in get_file_decoder_definitions():
        if defn.handler_type == handler_type:
            return defn.factory
    raise ValueError(f"Unsupported file-to-scene decoder type: {handler_type}")


def get_payload_decoder(handler_type: str) -> Any:
    """Look up and instantiate a payload-to-scene decoder for *handler_type*.

    Raises ``ValueError`` when no loaded plugin provides that handler type.
    """
    from .registry import PluginStatus, RuntimePluginRegistry

    for record in RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE):
        if record.status != PluginStatus.LOADED:
            continue
        definition = cast(DecoderPluginDefinition, record.definition)
        for payload_decoder in definition.payload_to_scene_decoders:
            if payload_decoder.handler_type == handler_type:
                return payload_decoder.decoder_factory()
    raise ValueError(f"Unsupported payload decoder handler type: {handler_type}")


def get_payload_filter_factory(handler_type: str) -> FilterFactory | None:
    """Return the plugin-provided filter factory for *handler_type*, if any."""
    from .registry import PluginStatus, RuntimePluginRegistry

    for record in RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE):
        if record.status != PluginStatus.LOADED:
            continue
        definition = cast(DecoderPluginDefinition, record.definition)
        for payload_decoder in definition.payload_to_scene_decoders:
            if payload_decoder.handler_type == handler_type:
                return payload_decoder.filter_factory
    return None


__all__ = [
    "DECODER_PLUGIN_TYPE",
    "DecoderPlugin",
    "DecoderPluginDefinition",
    "FileToSceneDecoderDefinition",
    "HandlerTypeName",
    "PayloadToSceneDecoderDefinition",
    "get_file_decoder_definitions",
    "get_file_decoder_factory",
    "get_payload_decoder",
    "get_payload_filter_factory",
]
