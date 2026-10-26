"""Scene store contracts and the persisted catalog metadata shared by the scene stores."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ax_devil.core.load_warning import LoadWarning
from ax_devil.core.source_paths import Fingerprint
from ax_devil.modules.data_sources.scene_history import SceneHistoryRecords
from ax_devil.modules.scene.decoding import (
    PayloadToSceneDecoder,
    PayloadToSceneDecoderFactory,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION, Scene
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_HISTORY_KEY = "history"
_WARNINGS_KEY = "load_warnings"


@dataclass(frozen=True, slots=True)
class SceneArtifactIdentity:
    """Complete identity of a persisted scene store artifact."""

    source_fingerprint: Fingerprint
    decoder_name: str
    artifact_version: int
    decode_options: dict[str, Any]
    scene_model_version: tuple[int, int] = SCENE_MODEL_VERSION

    def to_metadata(self) -> dict[str, Any]:
        """Return the JSON-compatible form persisted with the artifact."""
        return {
            "source_fingerprint": list(self.source_fingerprint),
            "decoder_name": self.decoder_name,
            "artifact_version": self.artifact_version,
            "scene_model_version": list(self.scene_model_version),
            "decode_options": self.decode_options,
        }


@dataclass(frozen=True, slots=True)
class SceneCatalog:
    """Catalog describing what a scene store can serve."""

    total_lines: int
    sequence_lookup_enabled: bool
    timestamp_to_sequence: dict[int, int]
    all_metadata: dict[str, Any]
    available_frame_ids: set[int]
    artifact_identity: SceneArtifactIdentity
    history: SceneHistoryRecords
    warnings: tuple[LoadWarning, ...]


@dataclass(frozen=True, slots=True)
class SceneBuildResult:
    """Result of building logical frame scenes from a source."""

    scene_map: dict[int, Scene]
    timestamp_to_sequence: dict[int, int]
    total_lines: int
    warnings: tuple[LoadWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class SceneStoreContext:
    """Named hooks and configuration required by a scene store."""

    decoder_name: str
    artifact_version: int
    decode_options: dict[str, Any]
    sequence_lookup_enabled: bool
    decoder_factory: PayloadToSceneDecoderFactory
    iter_payloads: Callable[[], Iterable[str]]
    normalize_payload: Callable[[str], str | None]
    build_scene_maps: Callable[[Iterable[str], PayloadToSceneDecoder], SceneBuildResult]
    collect_additional_cache_metadata: Callable[[], dict[str, Any]]
    restore_additional_state_from_cache: Callable[[dict[str, Any]], None]
    extract_timestamp_key: Callable[[Scene], int | None]
    extract_sequence_key: Callable[[Scene, int], int]


class SceneStore(Protocol):
    """Internal storage interface used by scene decoder providers."""

    @property
    def catalog(self) -> SceneCatalog:
        """Return the scene catalog for this store."""

    def load_scene(self, timestamp_key: int) -> Scene | None:
        """Load a scene by timestamp key."""

    def close(self) -> None:
        """Release all associated resources."""


def build_default_metadata_payload(
    *,
    total_lines: int,
    sequence_lookup_enabled: bool,
    timestamp_to_sequence: dict[int, int],
    artifact_identity: SceneArtifactIdentity,
    warnings: tuple[LoadWarning, ...],
) -> dict[str, Any]:
    """Build the common metadata payload for persisted store artifacts."""
    return {
        "total_lines": total_lines,
        _WARNINGS_KEY: [warning.to_metadata() for warning in warnings],
        "artifact_identity": artifact_identity.to_metadata(),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "sequence_lookup_enabled": sequence_lookup_enabled,
        "timestamp_to_sequence": {str(ts): seq for ts, seq in timestamp_to_sequence.items()},
    }


def persisted_metadata(metadata: dict[str, Any], history: SceneHistoryRecords) -> dict[str, Any]:
    """Return *metadata* with the history records added for persistence.

    Catalogs keep the parsed records rather than their JSON form, which would add thousands of live objects.
    """
    return {**metadata, _HISTORY_KEY: history.to_metadata()}


def build_scene_artifact_identity(
    *,
    context: SceneStoreContext,
    fingerprint: Fingerprint,
) -> SceneArtifactIdentity:
    """Combine source freshness with the provider-owned decode identity."""
    return SceneArtifactIdentity(
        source_fingerprint=fingerprint,
        decoder_name=context.decoder_name,
        artifact_version=context.artifact_version,
        decode_options=context.decode_options,
    )


def parse_scene_catalog(
    *,
    raw_metadata: dict[str, Any],
    cached_frames: Iterable[int],
    expected_artifact_identity: SceneArtifactIdentity,
    sequence_lookup_enabled: bool,
    restore_additional_state: Callable[[dict[str, Any]], None],
) -> SceneCatalog | None:
    """Validate persisted metadata and return a catalog when accepted."""
    if raw_metadata.get("artifact_identity") != expected_artifact_identity.to_metadata():
        return None

    timestamp_to_sequence_meta = raw_metadata.get("timestamp_to_sequence", {})
    try:
        timestamp_to_sequence = {int(ts): int(seq) for ts, seq in timestamp_to_sequence_meta.items()}
    except (TypeError, ValueError):
        return None

    history = SceneHistoryRecords.from_metadata(raw_metadata.pop(_HISTORY_KEY, None))
    if history is None:
        return None

    raw_warnings = raw_metadata.get(_WARNINGS_KEY)
    if not isinstance(raw_warnings, list):
        return None
    parsed_warnings = [LoadWarning.from_metadata(raw) for raw in raw_warnings]
    warnings = tuple(warning for warning in parsed_warnings if warning is not None)
    if len(warnings) != len(parsed_warnings):
        return None

    raw_metadata["sequence_lookup_enabled"] = sequence_lookup_enabled
    try:
        restore_additional_state(raw_metadata)
    except Exception as exc:
        logger.warning(f"Failed to restore cached scene store state: {exc}")
        return None

    return SceneCatalog(
        total_lines=int(raw_metadata.get("total_lines", 0)),
        sequence_lookup_enabled=sequence_lookup_enabled,
        timestamp_to_sequence=timestamp_to_sequence,
        all_metadata=raw_metadata,
        available_frame_ids=set(cached_frames),
        artifact_identity=expected_artifact_identity,
        history=history,
        warnings=warnings,
    )
