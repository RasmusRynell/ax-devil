"""Internal storage backends for decoder-backed scene file providers."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple

from ax_devil.core.atomic_file import open_replaceable, write_atomically
from ax_devil.core.load_warning import SkippedRecords
from ax_devil.core.source_paths import Fingerprint
from ax_devil.modules.cache import CacheManager, IndexedFrameCache
from ax_devil.modules.data_sources.file_data_provider.scene_catalog import (
    SceneCatalog,
    SceneStoreContext,
    build_default_metadata_payload,
    build_scene_artifact_identity,
    hash_file,
    hash_stream,
    parse_scene_catalog,
    persisted_metadata,
    stat_key,
)
from ax_devil.modules.data_sources.scene_history import SampleRow, SceneHistoryCollector
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


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
        self._cache = IndexedFrameCache(self._file_path, cache_type=cache_namespace)
        self._catalog = self._load_or_build()

    @property
    def catalog(self) -> SceneCatalog:
        return self._catalog

    def _load_or_build(self) -> SceneCatalog:
        if self._cache.exists():
            cached_metadata = self._cache.load_metadata()
            if cached_metadata:
                catalog = parse_scene_catalog(
                    raw_metadata=cached_metadata,
                    cached_frames=self._cache.available_frames(),
                    context=self._context,
                    source_stat=self._file_path.stat,
                    source_digest=lambda: hash_file(self._file_path),
                    republish=self._republish,
                )
                if catalog is not None:
                    logger.debug(
                        f"Accepted {len(catalog.available_frame_ids)} cached frame IDs for {self._file_path.name}"
                    )
                    return catalog

        return self._rebuild()

    def _republish(self, catalog: SceneCatalog) -> None:
        self._cache.replace_metadata(persisted_metadata(catalog.all_metadata, catalog.history))

    def _rebuild(self) -> SceneCatalog:
        logger.debug(f"Rebuilding indexed frame store for {self._file_path.name}")
        # Hashing and parsing open the path separately, so the stat key brackets both.
        stat_before = self._file_path.stat()
        content_digest = hash_file(self._file_path)
        payloads = self._context.iter_payloads()
        decoder = self._context.decoder_factory()
        build_result = self._context.build_scene_maps(payloads, decoder)
        collector = SceneHistoryCollector()
        for timestamp_key, scene in build_result.scene_map.items():
            collector.add(timestamp_key, scene)
        history = collector.records()
        # Windows has no change time, so restoring the original bytes, size and mtime during the parse window is
        # undetectable here; acceptable, since only that A->B->A-within-parse case is affected.
        if stat_key(self._file_path.stat()) != stat_key(stat_before):
            content_digest = ""  # Served from this open's cache, but the next open must not accept it.
        artifact_identity = build_scene_artifact_identity(
            context=self._context, source_stat=stat_before, content_digest=content_digest
        )
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


class _Row(NamedTuple):
    """What decoding one source line contributes to the index, independent of where the line sits in the file."""

    is_payload: bool  # The line held a payload, whether or not it decoded.
    skip_reason: str | None  # Why the line is skipped, without its "line N:" prefix.
    timestamp_key: int | None
    sequence_key: int | None  # None: the line's position among payload lines.
    sample: SampleRow | None


_BLANK_ROW = _Row(False, None, None, None, None)


class _PlainDataUnpickler(pickle.Unpickler):
    """Unpickler that refuses every global, so a cache file can only ever yield plain data."""

    def find_class(self, module: str, name: str) -> Any:
        raise pickle.UnpicklingError(f"Refusing to load {module}.{name} from a cache file")


def _line_key(raw_line: bytes) -> bytes:
    """Identify a line by its content; the line terminator never reaches the decoder."""
    return hashlib.sha256(raw_line.removesuffix(b"\n")).digest()[:16]


def _load_rows(path: Path, decode_metadata: dict[str, Any]) -> dict[bytes, _Row]:
    """Return the rows saved for lines decoded under *decode_metadata*, or none when the file cannot vouch for them."""
    try:
        with path.open("rb") as file_obj:
            payload = _PlainDataUnpickler(file_obj).load()
        if payload["decode"] != decode_metadata:
            return {}
        return {key: _Row(*row) for key, row in payload["rows"].items()}
    except Exception as exc:  # A damaged or foreign file only costs a full decode.
        logger.debug(f"Ignoring line cache {path.name}: {exc}")
        return {}


def _save_rows(path: Path, decode_metadata: dict[str, Any], rows: dict[bytes, _Row]) -> None:
    data = pickle.dumps(
        {"decode": decode_metadata, "rows": {key: tuple(row) for key, row in rows.items()}},
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    with write_atomically(path, "wb") as rows_file:
        rows_file.write(data)


class _IndexBuild:
    """The index of one source file, accumulated from its lines' rows in file order.

    `apply` is the only place that knows where a line sits in the file.
    """

    def __init__(self) -> None:
        self.total_lines = 0
        self.candidate_lines = 0  # Lines that held a record, whether or not it could be decoded.
        self.skipped = SkippedRecords("lines", "Objects on skipped lines are not shown.")
        self.timestamp_to_sequence: dict[int, int] = {}
        self.sequence_to_timestamp: dict[int, int] = {}
        self.line_locations: dict[int, tuple[int, int]] = {}
        self.collector = SceneHistoryCollector()

    def apply(self, row: _Row, line_number: int, offset: int, length: int) -> None:
        payload_index = self.total_lines
        if row.is_payload:
            self.total_lines += 1
        if row.is_payload or row.skip_reason is not None:
            self.candidate_lines += 1
        if row.skip_reason is not None:
            self.skipped.add(f"line {line_number}: {row.skip_reason}")
        if row.timestamp_key is None or row.sample is None:
            return

        timestamp_key = row.timestamp_key
        sequence_key = row.sequence_key if row.sequence_key is not None else payload_index
        self.timestamp_to_sequence[timestamp_key] = sequence_key
        previous_timestamp = self.sequence_to_timestamp.get(sequence_key)
        if previous_timestamp is not None and previous_timestamp != timestamp_key:
            logger.debug(f"Sequence {sequence_key} already mapped to {previous_timestamp}, updating to {timestamp_key}")
        self.sequence_to_timestamp[sequence_key] = timestamp_key
        self.line_locations[timestamp_key] = (offset, length)
        self.collector.add_row(timestamp_key, row.sample)


class SourceIndexedSceneStore:
    """Scene store that indexes timestamp keys back to raw source records.

    Use this when one source record can be re-read and decoded independently
    into one logical frame. With *reuse_records*, a rebuild after an edit decodes only the lines it has not
    decoded before; see ``SceneDecoderFileProvider`` for the contract that makes this safe.
    """

    def __init__(
        self,
        *,
        file_path: Path,
        cache_namespace: str,
        context: SceneStoreContext,
        reuse_records: bool = False,
    ) -> None:
        self._file_path = Path(file_path)
        self._context = context
        self._reuse_records = reuse_records
        self._decoder = context.decoder_factory()
        # Held from construction so the catalog describes exactly this inode, even if the path is replaced later.
        self._source_handle: BinaryIO | None = open_replaceable(self._file_path)
        self._source_changed = False
        cache_manager = CacheManager()
        self._index_path = cache_manager.get_cache_path(
            cache_type=cache_namespace,
            source_path=self._file_path,
            suffix=".scene-index.json",
        )
        self._rows_path = cache_manager.get_cache_path(
            cache_type=cache_namespace,
            source_path=self._file_path,
            suffix=".scene-lines.pickle",
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
            context=self._context,
            source_stat=self._source_stat,
            source_digest=lambda: hash_stream(self._open_source_handle()),
            republish=lambda refreshed: self._write_index(refreshed, line_locations),
        )
        if catalog is None:
            return None

        self._line_locations = line_locations
        logger.debug(f"Accepted {len(self._line_locations)} indexed source frames for {self._file_path.name}")
        return catalog

    def _rebuild(self) -> SceneCatalog:
        logger.debug(f"Building source index store for {self._file_path.name}")
        # Taken before reading, so a write during the build reads as newer than the cached artifact.
        source_stat = self._source_stat()
        # The digest is known only after the read; the decode part of the identity does not need it.
        pending_identity = build_scene_artifact_identity(
            context=self._context, source_stat=source_stat, content_digest=""
        )
        decode_metadata = pending_identity.decode_metadata()
        known = _load_rows(self._rows_path, decode_metadata) if self._reuse_records else {}
        rows: dict[bytes, _Row] = {}  # The row of every distinct line of this file.
        build = _IndexBuild()
        digest = hashlib.sha256()
        decoded_lines = 0

        file_obj = self._open_source_handle()
        file_obj.seek(0)
        line_number = 0
        while True:
            offset = file_obj.tell()
            raw_line = file_obj.readline()
            if not raw_line:
                break
            digest.update(raw_line)
            line_number += 1
            if self._reuse_records:
                key = _line_key(raw_line)
                row = rows.get(key) or known.get(key)
                if row is None:
                    row = self._decode_line(raw_line, line_number)
                    decoded_lines += 1
                rows[key] = row
            else:
                row = self._decode_line(raw_line, line_number)
                decoded_lines += 1
            build.apply(row, line_number, offset, len(raw_line))

        logger.debug(f"Reused {line_number - decoded_lines} of {line_number} lines of {self._file_path.name}")
        line_locations = build.line_locations
        self._line_locations = line_locations
        artifact_identity = replace(pending_identity, content_digest=digest.hexdigest())
        history = build.collector.records()
        warning = build.skipped.warning(build.candidate_lines)
        warnings = (warning,) if warning is not None else ()
        metadata = build_default_metadata_payload(
            total_lines=build.total_lines,
            sequence_lookup_enabled=self._context.sequence_lookup_enabled,
            timestamp_to_sequence=build.timestamp_to_sequence,
            artifact_identity=artifact_identity,
            warnings=warnings,
        )
        additional_metadata = self._context.collect_additional_cache_metadata()
        if additional_metadata:
            metadata.update(additional_metadata)
        catalog = SceneCatalog(
            total_lines=build.total_lines,
            sequence_lookup_enabled=self._context.sequence_lookup_enabled,
            timestamp_to_sequence=build.timestamp_to_sequence,
            all_metadata=metadata,
            available_frame_ids=set(line_locations.keys()),
            artifact_identity=artifact_identity,
            history=history,
            warnings=warnings,
        )
        # An edit during the parse leaves the index describing no single version of the file.
        if stat_key(self._source_stat()) == stat_key(source_stat):
            self._write_index(catalog, line_locations)
            if self._reuse_records and rows.keys() != known.keys():
                _save_rows(self._rows_path, decode_metadata, rows)
        return catalog

    def _decode_line(self, raw_line: bytes, line_number: int) -> _Row:
        """Decode one source line into its row."""
        where = f"{self._file_path.name} [{self._context.decoder_name}] line {line_number}"
        try:
            decoded_line = raw_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            logger.warning(f"{where}: {exc}")
            return _Row(False, "not valid UTF-8", None, None, None)

        normalized_line = self._context.normalize_payload(decoded_line)
        if normalized_line is None:
            return _BLANK_ROW

        try:
            scene = self._decoder.decode(normalized_line)
        except Exception as exc:
            logger.warning(f"{where}: {exc}")
            return _Row(True, str(exc), None, None, None)
        if scene is None:
            logger.debug(f"Decoder returned no scene for {where}, skipping")
            return _Row(True, None, None, None, None)

        timestamp_key = self._context.extract_timestamp_key(scene)
        if timestamp_key is None:
            logger.warning(f"{where}: scene has no timestamp, skipping")
            return _Row(True, None, None, None, None)
        return _Row(
            True,
            None,
            timestamp_key,
            self._context.extract_sequence_key(scene),
            SceneHistoryCollector.sample_row(scene),
        )

    def _write_index(self, catalog: SceneCatalog, line_locations: dict[int, tuple[int, int]]) -> None:
        payload = {
            "metadata": persisted_metadata(catalog.all_metadata, catalog.history),
            "line_locations": {str(key): [offset, length] for key, (offset, length) in line_locations.items()},
        }
        with write_atomically(self._index_path) as index_file:
            index_file.write(json.dumps(payload, separators=(",", ":")))

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

    def _source_stat(self) -> os.stat_result:
        return os.fstat(self._open_source_handle().fileno())

    def _source_fingerprint(self) -> Fingerprint:
        return Fingerprint.of_stat(self._source_stat())

    def close(self) -> None:
        if self._source_handle is not None:
            self._source_handle.close()
            self._source_handle = None
