"""Scene store contracts and the persisted catalog metadata shared by the scene stores."""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Protocol

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

# Bump when the persisted layout of a scene store artifact changes; older artifacts are rebuilt.
STORE_FORMAT_VERSION = 1
_HASH_CHUNK_BYTES = 1 << 20

# An unchanged stat key spares hashing the source only where ``st_ctime_ns`` is a change time. On Windows it is the
# creation time, so a same-size in-place edit that restores the mtime would be accepted stale; there the digest is
# checked on every open instead (about 22 ms per 54 MB).
STAT_SHORTCUT_IS_SOUND = sys.platform != "win32"


def stat_key(source_stat: os.stat_result) -> tuple[Fingerprint, int]:
    """Return the part of a ``stat`` result that changes when the file is written or replaced."""
    return Fingerprint.of_stat(source_stat), source_stat.st_ctime_ns


def hash_stream(file: BinaryIO) -> str:
    """Return the SHA-256 hex digest of everything *file* holds, read from its start."""
    digest = hashlib.sha256()
    file.seek(0)
    while chunk := file.read(_HASH_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def hash_file(file_path: Path) -> str:
    """Return the SHA-256 hex digest of the file's bytes."""
    with file_path.open("rb") as file:
        return hash_stream(file)


@dataclass(frozen=True, slots=True)
class SceneArtifactIdentity:
    """Complete identity of a persisted scene store artifact.

    The content digest decides whether the source is unchanged; the fingerprint and ``source_ctime_ns`` are a
    shortcut that spares hashing an unchanged file. ``ctime`` cannot be set from userland, so it exposes an edit that
    restores the modification time.
    """

    source_fingerprint: Fingerprint
    source_ctime_ns: int
    content_digest: str
    decoder_name: str
    artifact_version: int
    decode_options: dict[str, Any]
    scene_model_version: tuple[int, int] = SCENE_MODEL_VERSION
    store_format_version: int = STORE_FORMAT_VERSION

    def decode_metadata(self) -> dict[str, Any]:
        """Return the part of the identity that does not depend on the source file."""
        return {
            "decoder_name": self.decoder_name,
            "artifact_version": self.artifact_version,
            "scene_model_version": list(self.scene_model_version),
            "decode_options": self.decode_options,
            "store_format_version": self.store_format_version,
        }

    def to_metadata(self) -> dict[str, Any]:
        """Return the JSON-compatible form persisted with the artifact."""
        return {
            **self.decode_metadata(),
            "source_fingerprint": list(self.source_fingerprint),
            "source_ctime_ns": self.source_ctime_ns,
            "content_digest": self.content_digest,
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
    extract_sequence_key: Callable[[Scene], int | None]  # None: the record's position among payload lines.


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
    source_stat: os.stat_result,
    content_digest: str,
) -> SceneArtifactIdentity:
    """Combine source freshness with the provider-owned decode identity."""
    return SceneArtifactIdentity(
        source_fingerprint=Fingerprint.of_stat(source_stat),
        source_ctime_ns=source_stat.st_ctime_ns,
        content_digest=content_digest,
        decoder_name=context.decoder_name,
        artifact_version=context.artifact_version,
        decode_options=context.decode_options,
    )


def _resolve_artifact_identity(
    stored: object,
    *,
    context: SceneStoreContext,
    source_stat: Callable[[], os.stat_result],
    source_digest: Callable[[], str],
) -> SceneArtifactIdentity | None:
    """Return the identity of the current source when the *stored* identity still describes it.

    An unchanged stat key is accepted as is where ``STAT_SHORTCUT_IS_SOUND``. Any other is accepted only if the
    source's digest is still the stored one, and then the returned identity carries the new stat key.
    """
    if not isinstance(stored, dict):
        return None
    stored_digest = stored.get("content_digest")
    if not isinstance(stored_digest, str) or not stored_digest:  # Empty: written while the source changed.
        return None
    before = source_stat()
    current = build_scene_artifact_identity(context=context, source_stat=before, content_digest=stored_digest)
    if STAT_SHORTCUT_IS_SOUND and current.to_metadata() == stored:
        return current
    if any(stored.get(key) != value for key, value in current.decode_metadata().items()):
        return None
    if source_digest() != current.content_digest:
        return None
    if stat_key(source_stat()) != stat_key(before):
        return None  # Edited while it was hashed.
    return current


def parse_scene_catalog(
    *,
    raw_metadata: dict[str, Any],
    cached_frames: Iterable[int],
    context: SceneStoreContext,
    source_stat: Callable[[], os.stat_result],
    source_digest: Callable[[], str],
    republish: Callable[[SceneCatalog], None],
) -> SceneCatalog | None:
    """Validate persisted metadata and return a catalog when accepted.

    *source_stat* and *source_digest* describe the source the catalog will be served against. When the source's
    stat key changed but its content did not, the accepted artifact is handed to *republish* so that it carries the
    new stat key.
    """
    stored_identity = raw_metadata.get("artifact_identity")
    artifact_identity = _resolve_artifact_identity(
        stored_identity, context=context, source_stat=source_stat, source_digest=source_digest
    )
    if artifact_identity is None:
        return None
    raw_metadata["artifact_identity"] = artifact_identity.to_metadata()

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

    raw_metadata["sequence_lookup_enabled"] = context.sequence_lookup_enabled
    try:
        context.restore_additional_state_from_cache(raw_metadata)
    except Exception as exc:
        logger.warning(f"Failed to restore cached scene store state: {exc}")
        return None

    catalog = SceneCatalog(
        total_lines=int(raw_metadata.get("total_lines", 0)),
        sequence_lookup_enabled=context.sequence_lookup_enabled,
        timestamp_to_sequence=timestamp_to_sequence,
        all_metadata=raw_metadata,
        available_frame_ids=set(cached_frames),
        artifact_identity=artifact_identity,
        history=history,
        warnings=warnings,
    )
    if artifact_identity.to_metadata() != stored_identity:
        republish(catalog)
    return catalog
