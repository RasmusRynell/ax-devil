"""Small plugin contract for one text record per Scene, read from files."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import SceneDecoderFileProvider
from ax_devil.modules.filtering import FilterFactory
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Scene

from .decoder_plugins import DecoderPlugin, FileToSceneDecoderDefinition, PayloadToSceneDecoderDefinition


@dataclass(frozen=True)
class DecoderSpec:
    """Registration and source-index settings; declare supported versions as literals.

    ``decoder_name`` defaults to the plugin id; keep it stable to retain existing cache paths and identities.
    ``independent_records`` has the meaning documented on ``SceneDecoderFileProvider``.
    """

    id: str
    name: str
    handler: str
    api: int
    scene: tuple[int, int]
    extensions: tuple[str, ...]
    artifact_version: int
    decoder_name: str | None = None
    supports_sequence_lookup: bool = False
    independent_records: bool = False
    filter_factory: FilterFactory | None = None


class LineDecoderPlugin(DecoderPlugin):
    """Implement ``decode(str)`` and a frozen ``spec`` to decode text records from files.

    ``decode`` must not depend on earlier calls: the store reuses one decoder instance and decodes records in
    seek order, so a stateful decoder gives different output after a seek.
    """

    spec: ClassVar[DecoderSpec]

    @abstractmethod
    def decode(self, payload: str) -> Scene | None:
        """Decode one stripped, nonempty record; return None for no overlay, or raise for a malformed record."""

    @classmethod
    def plugin_id(cls) -> str:
        """Return the spec's plugin identity."""
        return cls.spec.id

    @classmethod
    def display_name(cls) -> str:
        """Return the spec's display name."""
        return cls.spec.name

    @classmethod
    def required_api_version(cls) -> int:
        """Return the plugin's explicitly supported API version."""
        return cls.spec.api

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        """Return the Scene model version emitted by this plugin."""
        return cls.spec.scene

    @classmethod
    def create_decoder(cls) -> PayloadToSceneDecoder:
        """Create a decoder that normalizes text and UTF-8 byte payloads."""
        return _LineDecoder(cls())

    @classmethod
    def create_provider(cls, file_path: str | Path) -> SceneDecoderFileProvider:
        """Open a source-indexed text file with this plugin's decoder and cache settings."""
        return SceneDecoderFileProvider(
            file_path,
            decoder_factory=cls.create_decoder,
            decoder_name=cls.spec.decoder_name if cls.spec.decoder_name is not None else cls.spec.id,
            artifact_version=cls.spec.artifact_version,
            supports_sequence_lookup=cls.spec.supports_sequence_lookup,
            independent_records=cls.spec.independent_records,
            filter_config_factory=cls.spec.filter_factory,
        )

    @classmethod
    def file_to_scene_decoders(cls) -> tuple[FileToSceneDecoderDefinition, ...]:
        """Publish the source-indexed file handler."""
        return (
            FileToSceneDecoderDefinition(
                handler_type=cls.spec.handler,
                factory=cls.create_provider,
                display_name=cls.spec.name,
                filter_factory=cls.spec.filter_factory,
                file_extensions=cls.spec.extensions,
            ),
        )

    @classmethod
    def payload_to_scene_decoders(cls) -> tuple[PayloadToSceneDecoderDefinition, ...]:
        """Publish the payload handler backed by a new decoder."""
        return (
            PayloadToSceneDecoderDefinition(
                handler_type=cls.spec.handler,
                decoder_factory=cls.create_decoder,
                display_name=cls.spec.name,
                filter_factory=cls.spec.filter_factory,
            ),
        )


class _LineDecoder(PayloadToSceneDecoder):
    def __init__(self, plugin: LineDecoderPlugin) -> None:
        self._plugin = plugin

    def decode(self, payload: object) -> Scene | None:
        """Normalize a text payload before calling the plugin instance."""
        if payload is None:
            return None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if not isinstance(payload, str):
            raise TypeError(f"{self._plugin.spec.handler} decoder expects text, got {type(payload)!r}")
        stripped = payload.strip()
        return self._plugin.decode(stripped) if stripped else None
