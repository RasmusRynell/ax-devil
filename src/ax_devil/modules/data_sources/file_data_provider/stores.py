"""Internal storage backends for decoder-backed scene file providers."""

from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, BinaryIO

from ax_devil.core.atomic_file import open_replaceable, write_atomically
from ax_devil.core.load_warning import SkippedRecords
from ax_devil.core.source_paths import Fingerprint
from ax_devil.modules.cache import CacheManager, IndexedFrameCache
from ax_devil.modules.data_sources.file_data_provider.scene_catalog import (
    SceneCatalog,
    SceneStoreContext,
    build_default_metadata_payload,
    build_scene_artifact_identity,
    parse_scene_catalog,
    persisted_metadata,
)
from ax_devil.modules.data_sources.scene_history import SceneHistoryCollector
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


def _variant_digest(context: SceneStoreContext) -> str:
    """Identify decode variants without source freshness, so revisions replace the same artifact."""
    identity = {
        "decoder_name": context.decoder_name,
        "artifact_version": context.artifact_version,
        "decode_options": context.decode_options,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class RuntimeSceneCache:
    """Retain two decoded scenes for consecutive playback and immediate revisits.

    Historical access belongs to the indexed store. Retaining hundreds of rich
    Scene graphs makes full garbage collections stall the shared GUI thread.
    """

    def __init__(self) -> None:
        self._entries: OrderedDict[int, Scene] = OrderedDict()

    def get(self, timestamp_key: int) -> Scene | None:
        """Return a retained scene and mark it most recently used."""
        scene = self._entries.get(timestamp_key)
        if scene is None:
            return None
        self._entries.move_to_end(timestamp_key)
        return scene

    def put(self, timestamp_key: int, scene: Scene) -> None:
        """Retain a scene, evicting the least recently used of more than two."""
        self._entries[timestamp_key] = scene
        self._entries.move_to_end(timestamp_key)
        while len(self._entries) > 2:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        """Release both retained scenes."""
        self._entries.clear()


class IndexedFrameSceneStore:
    """Scene store backed by persisted decoded frame artifacts.

    Use this for providers whose logical frames are produced by whole-file
    parsing, aggregation, or other preprocessing rather than direct per-record
    decoding from the source file.
    """

    def __init__(
        self,
        *,
        file_path: Path,
        cache_namespace: str,
        context: SceneStoreContext,
    ) -> None:
        self._file_path = Path(file_path)
        self._context = context
        self._cache = IndexedFrameCache(
            self._file_path, cache_type=cache_namespace, cache_suffix=f"-{_variant_digest(context)}.frames"
        )
        self._catalog = self._load_or_build()

    @property
    def catalog(self) -> SceneCatalog:
        return self._catalog

    def _load_or_build(self) -> SceneCatalog:
        if self._cache.exists():
            cached_metadata = self._cache.load_metadata()
            if cached_metadata:
                cached_frames = self._cache.available_frames()
                artifact_identity = build_scene_artifact_identity(
                    context=self._context,
                    fingerprint=Fingerprint.of_stat(self._file_path.stat()),
                )
                catalog = parse_scene_catalog(
                    raw_metadata=cached_metadata,
                    cached_frames=cached_frames,
                    expected_artifact_identity=artifact_identity,
                    sequence_lookup_enabled=self._context.sequence_lookup_enabled,
                    restore_additional_state=self._context.restore_additional_state_from_cache,
                )
                if catalog is not None:
                    logger.debug(
                        f"Accepted {len(catalog.available_frame_ids)} cached frame IDs for {self._file_path.name}"
                    )
                    return catalog

        return self._rebuild()

    def _rebuild(self) -> SceneCatalog:
        logger.debug(f"Rebuilding indexed frame store for {self._file_path.name}")
        # Taken before reading, so a write during the build reads as newer than the cached artifact.
        fingerprint = Fingerprint.of_stat(self._file_path.stat())
        payloads = self._context.iter_payloads()
        decoder = self._context.decoder_factory()
        build_result = self._context.build_scene_maps(payloads, decoder)
        artifact_identity = build_scene_artifact_identity(context=self._context, fingerprint=fingerprint)
        collector = SceneHistoryCollector()
        for timestamp_key, scene in build_result.scene_map.items():
            collector.add(timestamp_key, scene)
        history = collector.records()
        metadata = build_default_metadata_payload(
            total_lines=build_result.total_lines,
            sequence_lookup_enabled=self._context.sequence_lookup_enabled,
            timestamp_to_sequence=build_result.timestamp_to_sequence,
            artifact_identity=artifact_identity,
            warnings=build_result.warnings,
        )
        additional_metadata = self._context.collect_additional_cache_metadata()
        if additional_metadata:
            metadata.update(additional_metadata)
        self._cache.save(frames=build_result.scene_map, meta=persisted_metadata(metadata, history))
        return SceneCatalog(
            total_lines=build_result.total_lines,
            sequence_lookup_enabled=self._context.sequence_lookup_enabled,
            timestamp_to_sequence=build_result.timestamp_to_sequence,
            all_metadata=metadata,
            available_frame_ids=set(build_result.scene_map.keys()),
            artifact_identity=artifact_identity,
            history=history,
            warnings=build_result.warnings,
        )

    def load_scene(self, timestamp_key: int) -> Scene | None:
        return self._cache.load_frame(timestamp_key)

    def close(self) -> None:
        self._cache.close()


class SourceIndexedSceneStore:
    """Scene store that indexes timestamp keys back to raw source records.

    Use this when one source record can be re-read and decoded independently
    into one logical frame.
    """

    def __init__(
        self,
        *,
        file_path: Path,
        cache_namespace: str,
        context: SceneStoreContext,
    ) -> None:
        self._file_path = Path(file_path)
        self._context = context
        self._decoder = context.decoder_factory()
        # Held from construction so the catalog describes exactly this inode, even if the path is replaced later.
        self._source_handle: BinaryIO | None = open_replaceable(self._file_path)
        self._source_changed = False
        cache_manager = CacheManager()
        self._index_path = cache_manager.get_cache_path(
            cache_type=cache_namespace,
            source_path=self._file_path,
            suffix=f"-{_variant_digest(context)}.scene-index.json",
        )
        self._line_locations: dict[int, tuple[int, int]] = {}
        try:
            self._catalog = self._load_or_build()
        except BaseException:
            self.close()
            raise

    @property
    def catalog(self) -> SceneCatalog:
        return self._catalog

    def _load_or_build(self) -> SceneCatalog:
        if self._index_path.exists():
            try:
                raw_payload = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw_payload = None
            if isinstance(raw_payload, dict):
                catalog = self._restore_from_index_payload(raw_payload)
                if catalog is not None:
                    return catalog
        return self._rebuild()

    def _restore_from_index_payload(self, raw_payload: dict[str, Any]) -> SceneCatalog | None:
        raw_metadata = raw_payload.get("metadata")
        raw_locations = raw_payload.get("line_locations")
        if not isinstance(raw_metadata, dict) or not isinstance(raw_locations, dict):
            return None

        line_locations: dict[int, tuple[int, int]] = {}
        try:
            for key, value in raw_locations.items():
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    return None
                line_locations[int(key)] = (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            return None

        catalog = parse_scene_catalog(
            raw_metadata=raw_metadata,
            cached_frames=line_locations.keys(),
            expected_artifact_identity=build_scene_artifact_identity(
                context=self._context,
                fingerprint=self._source_fingerprint(),
            ),
            sequence_lookup_enabled=self._context.sequence_lookup_enabled,
            restore_additional_state=self._context.restore_additional_state_from_cache,
        )
        if catalog is None:
            return None

        self._line_locations = line_locations
        logger.debug(f"Accepted {len(self._line_locations)} indexed source frames for {self._file_path.name}")
        return catalog

    def _rebuild(self) -> SceneCatalog:
        logger.debug(f"Building source index store for {self._file_path.name}")
        # Taken before reading, so a write during the build reads as newer than the cached artifact.
        fingerprint = self._source_fingerprint()
        line_locations: dict[int, tuple[int, int]] = {}
        timestamp_to_sequence: dict[int, int] = {}
        sequence_to_timestamp: dict[int, int] = {}
        collector = SceneHistoryCollector()
        total_lines = 0
        candidate_lines = 0  # Lines that held a record, whether or not it could be decoded.
        skipped = SkippedRecords("lines", "Objects on skipped lines are not shown.")

        file_obj = self._open_source_handle()
        file_obj.seek(0)
        raw_line_number = 0
        while True:
            offset = file_obj.tell()
            raw_line = file_obj.readline()
            if not raw_line:
                break
            raw_line_number += 1
            try:
                decoded_line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                logger.warning(
                    f"{self._file_path.name} [{self._context.decoder_name}] line {raw_line_number - 1}: {exc}"
                )
                candidate_lines += 1
                skipped.add(f"line {raw_line_number}: not valid UTF-8")
                continue

            normalized_line = self._context.normalize_payload(decoded_line)
            if normalized_line is None:
                continue

            total_lines += 1
            candidate_lines += 1
            payload_index = total_lines - 1
            try:
                scene = self._decoder.decode(normalized_line)
                if scene is None:
                    logger.debug(f"Decoder returned no scene for line {payload_index}, skipping")
                    continue
            except Exception as exc:
                logger.warning(f"{self._file_path.name} [{self._context.decoder_name}] line {payload_index}: {exc}")
                skipped.add(f"line {raw_line_number}: {exc}")
                continue

            timestamp_key = self._context.extract_timestamp_key(scene)
            if timestamp_key is None:
                logger.warning(
                    f"{self._file_path.name} [{self._context.decoder_name}] line {payload_index}: "
                    "scene has no timestamp, skipping"
                )
                continue

            sequence_key = self._context.extract_sequence_key(scene, payload_index)
            timestamp_to_sequence[timestamp_key] = sequence_key
            previous_timestamp = sequence_to_timestamp.get(sequence_key)
            if previous_timestamp is not None and previous_timestamp != timestamp_key:
                logger.debug(
                    f"Sequence {sequence_key} already mapped to {previous_timestamp}, updating to {timestamp_key}"
                )
            sequence_to_timestamp[sequence_key] = timestamp_key
            line_locations[timestamp_key] = (offset, len(raw_line))
            collector.add(timestamp_key, scene)

        self._line_locations = line_locations
        artifact_identity = build_scene_artifact_identity(context=self._context, fingerprint=fingerprint)
        history = collector.records()
        warning = skipped.warning(candidate_lines)
        warnings = (warning,) if warning is not None else ()
        metadata = build_default_metadata_payload(
            total_lines=total_lines,
            sequence_lookup_enabled=self._context.sequence_lookup_enabled,
            timestamp_to_sequence=timestamp_to_sequence,
            artifact_identity=artifact_identity,
            warnings=warnings,
        )
        additional_metadata = self._context.collect_additional_cache_metadata()
        if additional_metadata:
            metadata.update(additional_metadata)
        payload = {
            "metadata": persisted_metadata(metadata, history),
            "line_locations": {str(key): [offset, length] for key, (offset, length) in line_locations.items()},
        }
        with write_atomically(self._index_path) as index_file:
            index_file.write(json.dumps(payload, separators=(",", ":")))
        return SceneCatalog(
            total_lines=total_lines,
            sequence_lookup_enabled=self._context.sequence_lookup_enabled,
            timestamp_to_sequence=timestamp_to_sequence,
            all_metadata=metadata,
            available_frame_ids=set(line_locations.keys()),
            artifact_identity=artifact_identity,
            history=history,
            warnings=warnings,
        )

    def load_scene(self, timestamp_key: int) -> Scene | None:
        if self._source_changed:
            return None
        location = self._line_locations.get(timestamp_key)
        if location is None:
            return None
        handle = self._open_source_handle()
        offset, length = location
        handle.seek(offset)
        raw_line = handle.read(length)
        # A writer that rewrites the file in place moves the offsets under the index; stop serving instead of
        # decoding the wrong bytes. A rename-replace leaves this inode untouched and keeps serving it.
        if self._source_fingerprint() != self._catalog.artifact_identity.source_fingerprint:
            self._source_changed = True
            logger.warning(f"{self._file_path.name} changed while open; no longer serving overlays from it")
            return None
        try:
            decoded_line = raw_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            logger.warning(
                f"{self._file_path.name} [{self._context.decoder_name}] indexed record {timestamp_key}: {exc}"
            )
            return None
        normalized_line = self._context.normalize_payload(decoded_line)
        if normalized_line is None:
            return None
        try:
            return self._decoder.decode(normalized_line)
        except Exception as exc:
            logger.warning(
                f"{self._file_path.name} [{self._context.decoder_name}] indexed record {timestamp_key}: {exc}"
            )
            return None

    def _open_source_handle(self) -> BinaryIO:
        if self._source_handle is None:
            raise ValueError(f"Scene store for {self._file_path.name} is closed")
        return self._source_handle

    def _source_fingerprint(self) -> Fingerprint:
        return Fingerprint.of_stat(os.fstat(self._open_source_handle().fileno()))

    def close(self) -> None:
        if self._source_handle is not None:
            self._source_handle.close()
            self._source_handle = None
