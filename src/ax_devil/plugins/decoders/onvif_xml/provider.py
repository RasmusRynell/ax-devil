"""ONVIF XML data provider for XML-per-line metadata files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.settings.logging_config import get_logger

from .decoder import build_onvif_filter_config, decode_onvif_xml

logger = get_logger(__name__)

__all__ = ["ONVIFXMLDataProvider", "ONVIFDecoder"]


class ONVIFDecoder(PayloadToSceneDecoder):
    """Decoder for ONVIF XML payloads (string/bytes)."""

    def decode(self, payload: Any) -> Scene | None:
        if payload is None:
            return None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if not isinstance(payload, str):
            raise TypeError(f"ONVIF decoder expects XML strings, got {type(payload)!r}")
        stripped = payload.strip()
        if not stripped:
            return None
        return decode_onvif_xml(stripped)


class ONVIFXMLDataProvider(SceneDecoderFileProvider):
    """Data provider for ONVIF XML metadata files with one XML document per line."""

    def __init__(
        self,
        file_path: str | Path,
        decoder_factory: type[PayloadToSceneDecoder] = ONVIFDecoder,
        decoder_name: str = "onvif_xml",
    ) -> None:
        super().__init__(
            file_path=file_path,
            decoder_factory=decoder_factory,
            decoder_name=decoder_name,
            artifact_version=1,
            supports_sequence_lookup=True,
            filter_config_factory=build_onvif_filter_config,
            storage_mode=StorageMode.SOURCE_INDEX,
        )
        logger.debug(f"ONVIF XML provider initialized for {self.file_path.name} with decoder '{decoder_name}'")
