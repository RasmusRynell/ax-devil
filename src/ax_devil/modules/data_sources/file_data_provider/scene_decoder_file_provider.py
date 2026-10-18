"""Utilities for turning decoder-ready text files into inspectable :class:`Scene` sources."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.data_types import FrameIdentifier
from ax_devil.core.load_warning import SkippedRecords
from ax_devil.core.source_paths import Fingerprint
from ax_devil.modules.data_sources.file_data_provider.base import FrameIdentifierDataProvider
from ax_devil.modules.data_sources.file_data_provider.lookup_metadata import (
    OverlayLookupMatchType,
    OverlayLookupResult,
)
from ax_devil.modules.data_sources.file_data_provider.stores import (
    IndexedFrameSceneStore,
    RuntimeSceneCache,
    SceneBuildResult,
    SceneCatalog,
    SceneStore,
    SceneStoreContext,
    SourceIndexedSceneStore,
)
from ax_devil.modules.data_sources.overlay_samples import OverlaySamples, sequence_sample
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentBasis
from ax_devil.modules.filtering.filter_config import FilterConfig, build_default_filter_config
from ax_devil.modules.scene.decoding import (
    PayloadToSceneDecoder,
    PayloadToSceneDecoderFactory,
)
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.synchronization.timestamp_matching import (
    TimestampFallbackPolicy,
    find_matching_overlay_timestamp,
)

logger = get_logger(__name__)


class StorageMode(str, Enum):
    """Internal provider storage strategy.

    `SOURCE_INDEX`
        Persist only an index into the original source file, then re-read and
        decode one source record on demand.

    `DERIVED_CACHE`
        Persist decoded frame artifacts because logical frames cannot be
        recovered cheaply from one source record alone.
    """

    SOURCE_INDEX = "source_index"
    DERIVED_CACHE = "derived_cache"


@dataclass(frozen=True, slots=True)
class SceneDecoderFileMetadata:
    """Metadata captured for a decoder-driven scene file."""

    file_path: str
    total_lines: int
    decoder_name: str
    source_fingerprint: Fingerprint
    sequence_lookup_enabled: bool
    timestamp_to_sequence: dict[int, int]
    all_metadata: dict[str, Any]


class SceneDecoderFileProvider(FrameIdentifierDataProvider):
    """Common functionality for file sources decoded into :class:`Scene` objects."""

    def __init__(
        self,
        file_path: str | Path,
        *,
        decoder_factory: PayloadToSceneDecoderFactory,
        decoder_name: str = "custom",
        artifact_version: int,
        decode_options: Mapping[str, Any] | None = None,
        supports_sequence_lookup: bool = False,
        filter_config_factory: Callable[[], FilterConfig] | None = None,
        cache_namespace: str | None = None,
        storage_mode: StorageMode = StorageMode.SOURCE_INDEX,
        timestamp_fallback_policy: TimestampFallbackPolicy | None = None,
    ) -> None:
        """Initialize a decoder-backed file provider.

        Args:
            file_path: Source file containing overlay payload data.
            decoder_factory: Factory creating a decoder for one payload record.
            decoder_name: Stable decoder identifier used in metadata and cache paths.
            artifact_version: Bump when this provider's decoded output changes. Scene model changes are
                covered by ``SCENE_MODEL_VERSION`` in the cache identity.
            decode_options: Provider-owned options that change persisted Scene output.
            supports_sequence_lookup: Whether lookups may fall back from timestamp
                to ``FrameIdentifier.sequence_id``.
            filter_config_factory: Optional decoder-specific filter config factory.
            cache_namespace: Cache namespace for persisted provider artifacts.
            storage_mode:
                ``StorageMode.SOURCE_INDEX`` stores an index into the original
                source file and decodes records on demand.
                ``StorageMode.DERIVED_CACHE`` stores decoded frame artifacts for
                providers that require whole-file parsing or aggregation.
            timestamp_fallback_policy: Timestamp fallback policy used after
                exact timestamp lookup and provider-owned sequence lookup fail.
        """
        self.file_path = Path(file_path)
        self.decoder_name = str(decoder_name)
        self._artifact_version = int(artifact_version)
        if self._artifact_version < 1:
            raise ValueError("Artifact version must be at least 1.")
        self._decode_options = dict(decode_options or {})
        self._filter_config_factory = filter_config_factory
        self._cache_namespace = str(cache_namespace) if cache_namespace is not None else self.decoder_name
        self._timestamp_fallback_policy = timestamp_fallback_policy or TimestampFallbackPolicy()
        self._decoder_factory = decoder_factory
        self._sequence_lookup_enabled = supports_sequence_lookup
        self._storage_mode = storage_mode
        self._runtime_cache = RuntimeSceneCache()
        self._metadata: SceneDecoderFileMetadata | None = None
        self._available_frame_ids: set[int] = set()
        self._sorted_frame_ids: tuple[int, ...] = ()
        self._sequence_to_timestamp: dict[int, int] = {}
        self._timestamp_to_sequence: dict[int, int] = {}
        self._store = self._build_store()
        self._apply_catalog(self._store.catalog)

        name = self.file_path.name
        if self._sorted_frame_ids:
            n = len(self._sorted_frame_ids)
            lo, hi = self._sorted_frame_ids[0], self._sorted_frame_ids[-1]
            logger.debug(f"{name} [{decoder_name}] loaded {n} scenes, timestamp range [{lo} .. {hi}] µs")
        else:
            logger.warning(f"{name} [{decoder_name}] loaded 0 scenes")

    def _build_store(self) -> SceneStore:
        context = SceneStoreContext(
            decoder_name=self.decoder_name,
            artifact_version=self._artifact_version,
            decode_options=self._decode_options,
            sequence_lookup_enabled=self._sequence_lookup_enabled,
            decoder_factory=self._decoder_factory,
            iter_payloads=self._iter_payloads,
            normalize_payload=self._normalize_payload,
            build_scene_maps=self._build_scene_maps,
            collect_additional_cache_metadata=self._collect_additional_cache_metadata,
            restore_additional_state_from_cache=self._restore_additional_state_from_cache,
            extract_timestamp_key=self._extract_timestamp_key,
            extract_sequence_key=self._extract_sequence_key,
        )
        if self._storage_mode is StorageMode.DERIVED_CACHE:
            return IndexedFrameSceneStore(
                file_path=self.file_path,
                cache_namespace=self._cache_namespace,
                context=context,
            )

        return SourceIndexedSceneStore(
            file_path=self.file_path,
            cache_namespace=self._cache_namespace,
            context=context,
        )

    def _apply_catalog(self, catalog: SceneCatalog) -> None:
        self._available_frame_ids = set(catalog.available_frame_ids)
        self._sorted_frame_ids = tuple(sorted(self._available_frame_ids))
        self._timestamp_to_sequence = dict(catalog.timestamp_to_sequence)
        self._sequence_to_timestamp = {seq: ts for ts, seq in self._timestamp_to_sequence.items()}
        self._sorted_sequences = tuple(sorted(self._sequence_to_timestamp))
        self._samples = OverlaySamples(
            sequence_lookup=self._sequence_lookup_enabled,
            timestamps_us=self._sorted_frame_ids,
            sequences=self._sorted_sequences,
            sequence_to_timestamp=self._sequence_to_timestamp,
            timestamp_to_sequence=self._timestamp_to_sequence,
            records=catalog.history,
            policy=self._timestamp_fallback_policy,
            load_warnings=catalog.warnings,
        )
        self._metadata = SceneDecoderFileMetadata(
            file_path=str(self.file_path),
            total_lines=catalog.total_lines,
            decoder_name=self.decoder_name,
            source_fingerprint=catalog.artifact_identity.source_fingerprint,
            sequence_lookup_enabled=self._sequence_lookup_enabled,
            timestamp_to_sequence=self._timestamp_to_sequence.copy(),
            all_metadata=dict(catalog.all_metadata),
        )

    def _normalize_payload(self, raw_payload: str) -> str | None:
        """Normalize a raw payload string from the source file."""
        line = raw_payload.rstrip("\n").strip()
        if not line:
            return None
        return line

    def _iter_payloads(self) -> Iterable[str]:
        """Yield normalized payload strings from the source file."""
        try:
            with self.file_path.open("r", encoding="utf-8") as file_obj:
                for raw_line in file_obj:
                    if normalized_line := self._normalize_payload(raw_line):
                        yield normalized_line
        except Exception as exc:  # pragma: no cover - propagate to caller/tests
            logger.error(f"Failed to stream payloads from {self.file_path}: {exc}")
            raise

    def _collect_additional_cache_metadata(self) -> dict[str, Any]:
        """Return extra metadata to persist alongside the default payload."""
        return {}

    def _restore_additional_state_from_cache(self, raw_metadata: dict[str, Any]) -> None:
        """Rehydrate subclass-specific state from cached metadata."""
        return

    def _build_scene_maps(
        self,
        payloads: Iterable[str],
        decoder: PayloadToSceneDecoder,
    ) -> SceneBuildResult:
        """Produce scene/timestamp/sequence maps from the decoded payloads."""
        decoded_scenes: list[tuple[int, Scene]] = []
        total_lines = 0
        skipped = SkippedRecords("records", "Objects in skipped records are not shown.")

        for line_num, payload in enumerate(payloads):
            total_lines += 1
            try:
                scene = decoder.decode(payload)
                if scene is None:
                    logger.debug(f"Decoder returned no scene for line {line_num}, skipping")
                    continue
                decoded_scenes.append((line_num, scene))
            except Exception as exc:
                logger.warning(f"Line {line_num}: {exc}")
                skipped.add(f"record {line_num + 1}: {exc}")
                continue

        result = self._build_scene_result_from_scenes(decoded_scenes, total_lines=total_lines)
        warning = skipped.warning(total_lines)
        return replace(result, warnings=(warning,)) if warning is not None else result

    def _build_scene_result_from_scenes(
        self,
        scenes: Iterable[tuple[int, Scene]],
        *,
        total_lines: int,
    ) -> SceneBuildResult:
        """Build timestamp and sequence maps from already-decoded scenes."""
        scene_map: dict[int, Scene] = {}
        timestamp_to_sequence: dict[int, int] = {}
        sequence_to_timestamp: dict[int, int] = {}

        for line_num, scene in scenes:
            timestamp_key = self._extract_timestamp_key(scene)
            if timestamp_key is None:
                logger.warning(f"Scene at line {line_num} has no timestamp, skipping")
                continue

            sequence_key = self._extract_sequence_key(scene, fallback_sequence=line_num)
            scene_map[timestamp_key] = scene
            timestamp_to_sequence[timestamp_key] = sequence_key

            previous_timestamp = sequence_to_timestamp.get(sequence_key)
            if previous_timestamp is not None and previous_timestamp != timestamp_key:
                logger.debug(
                    f"Sequence {sequence_key} already mapped to {previous_timestamp}, updating to {timestamp_key}"
                )
            sequence_to_timestamp[sequence_key] = timestamp_key

        return SceneBuildResult(
            scene_map=scene_map,
            timestamp_to_sequence=timestamp_to_sequence,
            total_lines=total_lines,
        )

    def _load_scene(self, timestamp_key: int) -> Scene | None:
        cached_scene = self._runtime_cache.get(timestamp_key)
        if cached_scene is not None:
            return cached_scene
        if timestamp_key not in self._available_frame_ids:
            return None
        scene = self._store.load_scene(timestamp_key)
        if scene is not None:
            self._runtime_cache.put(timestamp_key, scene)
        return scene

    def load_by_frame_id(self, frame_identifier: FrameIdentifier) -> Scene | None:
        """Load scene data using FrameIdentifier."""
        return self.lookup_by_frame_id(frame_identifier).scene

    def lookup_by_frame_id(
        self,
        frame_identifier: FrameIdentifier,
        *,
        allow_previous: bool = False,
        frame_timeline: FrameTimeline | None = None,
    ) -> OverlayLookupResult:
        """Load scene data using FrameIdentifier with timestamp and optional sequence lookup.

        Frame-keyed providers use frame_timeline to resolve annotation sequence IDs
        into video time. Otherwise lookup uses exact timestamps, optional sequence
        fallback, then timestamp matching. With allow_previous, select the latest
        earlier sample beyond matching tolerance; the caller applies its age limit.
        """
        if self._sequence_lookup_enabled:
            if frame_timeline is not None:
                return self._lookup_video_sequence(frame_identifier, frame_timeline, allow_previous=allow_previous)
            if allow_previous:
                raise ValueError("Sticky sequence overlays require the video's frame timeline")

        timestamp_key = int(frame_identifier.timestamp_monotime_us)

        scene = self._load_scene(timestamp_key)
        if scene is not None:
            result = self._lookup_result(
                scene=scene,
                requested_timestamp_us=timestamp_key,
                matched_timestamp_us=timestamp_key,
                requested_sequence_id=frame_identifier.sequence_id,
                matched_sequence_id=self._timestamp_to_sequence.get(timestamp_key),
                match_type="exact",
            )
            return result

        if self._sequence_lookup_enabled:
            ts_from_seq = self._sequence_to_timestamp.get(frame_identifier.sequence_id)
            if ts_from_seq is not None and ts_from_seq <= timestamp_key:
                scene = self._load_scene(ts_from_seq)
                if scene is not None:
                    result = self._lookup_result(
                        scene=scene,
                        requested_timestamp_us=timestamp_key,
                        matched_timestamp_us=ts_from_seq,
                        requested_sequence_id=frame_identifier.sequence_id,
                        matched_sequence_id=frame_identifier.sequence_id,
                        alignment_basis="sequence",
                        match_type="sequence",
                    )
                    return result

        match = find_matching_overlay_timestamp(
            sorted_overlay_timestamps_us=self._sorted_frame_ids,
            video_timestamp_us=timestamp_key,
            policy=self._timestamp_fallback_policy,
            allow_previous=allow_previous,
        )
        if match.overlay_timestamp_us is not None:
            scene = self._load_scene(match.overlay_timestamp_us)
            if scene is not None:
                result = self._lookup_result(
                    scene=scene,
                    requested_timestamp_us=timestamp_key,
                    matched_timestamp_us=match.overlay_timestamp_us,
                    requested_sequence_id=frame_identifier.sequence_id,
                    matched_sequence_id=self._timestamp_to_sequence.get(match.overlay_timestamp_us),
                    match_type=match.match_type,
                )
                return result

        result = self._lookup_result(
            scene=None,
            requested_timestamp_us=timestamp_key,
            matched_timestamp_us=None,
            requested_sequence_id=frame_identifier.sequence_id,
            matched_sequence_id=None,
            match_type="missing",
        )
        return result

    def _lookup_video_sequence(
        self, frame_id: FrameIdentifier, timeline: FrameTimeline, *, allow_previous: bool
    ) -> OverlayLookupResult:
        """Resolve frame-keyed annotations using actual video timestamps for their age."""
        requested_time = int(frame_id.timestamp_monotime_us)
        sample = sequence_sample(
            self._sorted_sequences, frame_id.sequence_id, requested_time, timeline, allow_previous=allow_previous
        )
        sequence, sample_time = sample if sample is not None else (None, None)
        scene = self._load_scene(self._sequence_to_timestamp[sequence]) if sequence is not None else None
        match_type: OverlayLookupMatchType = "missing"
        if scene is not None:
            match_type = "sequence" if sequence == frame_id.sequence_id else "retained"
        result = self._lookup_result(
            scene=scene,
            requested_timestamp_us=requested_time,
            matched_timestamp_us=sample_time if scene is not None else None,
            requested_sequence_id=frame_id.sequence_id,
            matched_sequence_id=sequence if scene is not None else None,
            match_type=match_type,
            alignment_basis="sequence",
        )
        return result

    def _lookup_result(
        self,
        *,
        scene: Scene | None,
        requested_timestamp_us: int,
        matched_timestamp_us: int | None,
        requested_sequence_id: int | None,
        matched_sequence_id: int | None,
        match_type: OverlayLookupMatchType,
        alignment_basis: OverlayAlignmentBasis = "timestamp",
    ) -> OverlayLookupResult:
        """Return a lookup result using the current timestamp policy."""
        return OverlayLookupResult(
            scene=scene,
            requested_timestamp_us=requested_timestamp_us,
            matched_timestamp_us=matched_timestamp_us,
            match_type=match_type,
            timestamp_fallback_policy=self._timestamp_fallback_policy,
            requested_sequence_id=requested_sequence_id,
            matched_sequence_id=matched_sequence_id,
            alignment_basis=alignment_basis,
        )

    def get_timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        """Return the timestamp fallback policy used after exact and sequence lookup fail."""
        return self._timestamp_fallback_policy

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        """Update timestamp fallback policy without rebuilding the provider.

        Sequence lookup remains controlled by ``supports_sequence_lookup`` and
        is not affected by this policy.
        """
        self._timestamp_fallback_policy = policy
        self._samples = replace(self._samples, policy=policy)

    def get_available_frames(self) -> set[int]:
        """Get the set of available frame timestamps (as monotime microseconds)."""
        return set(self._available_frame_ids)

    def get_total_frames(self) -> int:
        """Get the total number of frames available."""
        return len(self._available_frame_ids)

    def samples(self) -> OverlaySamples:
        """Return this file's sample keys, history and current timestamp policy as an immutable value."""
        return self._samples

    def scene_history(
        self, video_timeline: FrameTimeline, *, allow_previous: bool, max_sample_age_us: int | None, token: CancelToken
    ) -> SceneHistory:
        """Return this source's events and entity appearances placed on the video's frames; see `OverlaySamples`."""
        return self._samples.scene_history(
            video_timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us, token=token
        )

    def get_metadata(self) -> SceneDecoderFileMetadata:
        """Get metadata about the data file."""
        if self._metadata is None:
            raise RuntimeError("Metadata not available - file may not have been processed")
        return self._metadata

    def close(self) -> None:
        """Close the provider and clean up resources."""
        self._log_parse_summary()
        self._runtime_cache.clear()
        self._store.close()

    def _log_parse_summary(self) -> None:
        """Log records that did not produce a scene."""
        total_lines = self._metadata.total_lines if self._metadata else 0
        total_available = len(self._available_frame_ids)
        skipped_at_parse = total_lines - total_available
        name = self.file_path.name
        dec = self.decoder_name

        if skipped_at_parse > 0:
            logger.debug(
                f"{name} [{dec}] PARSE GAP: {skipped_at_parse}/{total_lines} payload lines did not produce a scene"
            )

    def _extract_timestamp_key(self, scene: Scene) -> int | None:
        """Derive a monotonic timestamp key for a scene."""
        start = scene.time_slice.start
        if isinstance(start, datetime):
            return int(start.timestamp() * 1_000_000)
        if isinstance(start, (int, float)):
            return int(start)
        return None

    def _extract_sequence_key(self, scene: Scene, fallback_sequence: int) -> int:
        """Determine the sequence identifier for a scene."""
        start = scene.time_slice.start
        if isinstance(start, int):
            return int(start)
        return fallback_sequence

    def get_filter_config(self) -> FilterConfig:
        """Get a default filter configuration."""
        if self._filter_config_factory is not None:
            return self._filter_config_factory()
        logger.debug(f"Using default filter config for {self.file_path.name}")
        return build_default_filter_config()
