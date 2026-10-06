"""Scene decoder adapter for CVAT XML annotations."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.data_sources.file_data_provider.stores import SceneBuildResult
from ax_devil.modules.filtering.filter_config import FilterConfig
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.settings.logging_config import get_logger

from .decoder import (
    CVATFileMetadata as CVATMetadata,
)
from .decoder import (
    CVATParseResult,
    build_cvat_filter_config,
    decode_cvat_frame,
    parse_cvat_document,
)

logger = get_logger(__name__)

__all__ = ["CVATSceneDataProvider", "CVATMetadata", "CVATDecoder"]


def _metadata_to_payload(metadata: CVATMetadata) -> Dict[str, Any]:
    """Serialisable payload for storing CVAT metadata in the cache."""
    payload = asdict(metadata)
    payload["labels"] = list(metadata.labels)
    return payload


def _payload_to_metadata(raw_payload: Any) -> CVATMetadata | None:
    if not isinstance(raw_payload, dict):
        return None
    try:
        labels_payload = raw_payload.get("labels", [])
        labels_tuple = tuple(str(label) for label in labels_payload)
        return CVATMetadata(
            width=int(raw_payload["width"]),
            height=int(raw_payload["height"]),
            task_name=str(raw_payload.get("task_name", "")),
            start_frame=int(raw_payload.get("start_frame", 0)),
            stop_frame=int(raw_payload.get("stop_frame", 0)),
            size=int(raw_payload.get("size", 0)),
            labels=labels_tuple,
            all_metadata=dict(raw_payload.get("all_metadata", {})),
        )
    except (KeyError, TypeError, ValueError):
        return None


class CVATDecoder(PayloadToSceneDecoder):
    """Decoder for serialised CVAT frame payloads (JSON strings)."""

    def decode(self, payload: Any) -> Scene | None:
        if payload is None:
            return None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if not isinstance(payload, str):
            raise TypeError(f"CVAT decoder expects JSON strings, got {type(payload)!r}")
        stripped = payload.strip()
        if not stripped:
            return None
        return decode_cvat_frame(stripped)


class CVATSceneDataProvider(SceneDecoderFileProvider):
    """Scene decoder file provider for CVAT XML annotations."""

    def __init__(self, xml_file: str | Path) -> None:
        self._cvat_metadata: CVATMetadata | None = None
        self._labels: Tuple[str, ...] = ()

        super().__init__(
            file_path=xml_file,
            decoder_factory=CVATDecoder,
            decoder_name="cvat_xml",
            artifact_version=2,
            supports_sequence_lookup=True,
            filter_config_factory=self._build_filter_config,
            storage_mode=StorageMode.DERIVED_CACHE,
        )

    # ------------------------------------------------------------------ #
    # SceneDecoder hooks                                                 #
    # ------------------------------------------------------------------ #

    def _build_scene_maps(
        self,
        payloads: Iterable[str],
        decoder: PayloadToSceneDecoder,
    ) -> SceneBuildResult:
        del payloads
        parse_result: CVATParseResult = parse_cvat_document(self.file_path)
        self._cvat_metadata = parse_result.metadata
        self._labels = parse_result.metadata.labels

        return super()._build_scene_maps(parse_result.payloads, decoder)

    def _collect_additional_cache_metadata(self) -> Dict[str, Any]:
        if self._cvat_metadata is None:
            return {}
        return {"cvat_metadata": _metadata_to_payload(self._cvat_metadata)}

    def _restore_additional_state_from_cache(self, raw_metadata: Dict[str, Any]) -> None:
        metadata_payload = raw_metadata.get("cvat_metadata")
        cvat_metadata = _payload_to_metadata(metadata_payload)
        if cvat_metadata is None:
            logger.debug(f"CVAT cache metadata missing for {self.file_path}; triggering cache regeneration.")
            raise RuntimeError("CVAT cache metadata missing required fields.")
        self._cvat_metadata = cvat_metadata
        self._labels = cvat_metadata.labels

    # ------------------------------------------------------------------ #
    # Metadata access                                                    #
    # ------------------------------------------------------------------ #

    def _require_cvat_metadata(self) -> CVATMetadata:
        if self._cvat_metadata is None:
            raise RuntimeError("CVAT metadata not available; parse may have failed.")
        return self._cvat_metadata

    def get_cvat_metadata(self) -> CVATMetadata:
        """Return CVAT-specific metadata captured during parsing."""
        return self._require_cvat_metadata()

    def get_metadata(self) -> CVATMetadata:  # type: ignore[override]
        """Return CVAT-specific metadata captured during parsing."""
        return self._require_cvat_metadata()

    def get_metadata_value(self, path: str) -> Any:
        """Resolve a metadata value by dot-separated path."""
        metadata = self._require_cvat_metadata().all_metadata
        current: Any = metadata
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                if index >= len(current):
                    return None
                current = current[index]
            else:
                return None
        return current

    def list_metadata_paths(self) -> List[str]:
        """List available metadata paths for discovery."""
        metadata = self._require_cvat_metadata().all_metadata
        paths: List[str] = []

        def build_paths(node: Any, prefix: str = "") -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    next_prefix = f"{prefix}.{key}" if prefix else key
                    paths.append(next_prefix)
                    build_paths(value, next_prefix)
            elif isinstance(node, list):
                for idx, value in enumerate(node):
                    next_prefix = f"{prefix}.{idx}" if prefix else str(idx)
                    build_paths(value, next_prefix)

        build_paths(metadata)
        return sorted(paths)

    # ------------------------------------------------------------------ #
    # Filtering                                                          #
    # ------------------------------------------------------------------ #

    def get_total_frames(self) -> int:
        """Return the full dataset size so sparse annotations still seek correctly."""
        metadata_size = self._require_cvat_metadata().size
        if metadata_size > 0:
            return metadata_size
        return super().get_total_frames()

    def _build_filter_config(self) -> FilterConfig:
        labels = self._labels or ()
        return build_cvat_filter_config(labels if labels else ("",))
