"""Tests for JSONL data provider."""

import json
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import call, patch

import pytest

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.cache import CacheManager, IndexedFrameCache
from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.data_sources.file_data_provider.stores import SceneBuildResult, SourceFingerprint
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy


def simple_test_decoder(json_string: str) -> Scene:
    """Simple test decoder for testing purposes."""
    from ax_devil.modules.scene.model import Observation

    data = json.loads(json_string)
    timestamp = datetime.fromisoformat(data.get("timestamp", "2024-01-01T12:00:00Z").replace("Z", "+00:00"))

    entities = {}
    for detection in data.get("detections", []):
        entity_id = EntityId(detection["id"])
        bbox = BoundingBox.from_xywh(*detection["bbox"])
        classification = Classification(type=detection["class"], score=Score(detection["confidence"]))

        observation = Observation(timestamp=timestamp, geometry=bbox, classification=[classification])

        entity = Entity(id=entity_id, observations=[observation])
        entities[entity_id] = entity

    return Scene(time_slice=TimeSlice(start=timestamp, end=timestamp), entities=entities)


def _build_provider(
    file_path: str | Path,
    *,
    supports_sequence_lookup: bool = False,
    storage_mode: StorageMode = StorageMode.SOURCE_INDEX,
    artifact_version: int = 1,
    decode_options: dict[str, Any] | None = None,
    provider_type: type[SceneDecoderFileProvider] = SceneDecoderFileProvider,
    timestamp_fallback_policy: TimestampFallbackPolicy = TimestampFallbackPolicy(
        tolerance_us=0,
    ),
) -> SceneDecoderFileProvider:
    class _TestDecoder(PayloadToSceneDecoder):
        def decode(self, payload: Any) -> Scene:
            return simple_test_decoder(payload)

    return provider_type(
        file_path=file_path,
        decoder_factory=_TestDecoder,
        decoder_name="test_decoder",
        artifact_version=artifact_version,
        decode_options=decode_options,
        supports_sequence_lookup=supports_sequence_lookup,
        storage_mode=storage_mode,
        timestamp_fallback_policy=timestamp_fallback_policy,
    )


class TestJSONLDataProvider:
    """Test cases for JSONL data provider."""

    def test_jsonl_provider_basic_functionality(self) -> None:
        """Test basic JSONL provider functionality."""
        test_data = [
            {
                "timestamp": "2024-01-01T12:00:00Z",
                "detections": [
                    {
                        "id": "person_1",
                        "class": "person",
                        "confidence": 0.95,
                        "bbox": [0.1, 0.2, 0.3, 0.4],
                        "attributes": {"color": "blue"},
                    }
                ],
            },
            {
                "timestamp": "2024-01-01T12:00:01Z",
                "detections": [
                    {
                        "id": "car_1",
                        "class": "car",
                        "confidence": 0.88,
                        "bbox": [0.5, 0.6, 0.2, 0.3],
                        "attributes": {"color": "red"},
                    }
                ],
            },
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for data in test_data:
                f.write(json.dumps(data) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    # Create provider
                    provider = _build_provider(temp_file)

                    # Test basic functionality
                    assert provider.get_total_frames() == 2
                    available_frames = provider.get_available_frames()
                    # Should have timestamp keys instead of line numbers
                    assert len(available_frames) == 2
                    # Check that all keys are positive integers (timestamp microseconds)
                    assert all(isinstance(key, int) and key > 0 for key in available_frames)

                    # Test loading scenes using actual timestamp keys
                    from ax_devil.core.data_types import FrameIdentifier

                    # Get the actual timestamp keys
                    timestamp_keys = list(available_frames)

                    # Test loading first scene
                    frame_id_0 = FrameIdentifier(sequence_id=0, timestamp_monotime_us=float(timestamp_keys[0]))
                    scene_0 = provider.load_by_frame_id(frame_id_0)
                    assert scene_0 is not None
                    assert isinstance(scene_0, Scene)
                    assert len(scene_0.entities) == 1
                    assert EntityId("person_1") in scene_0.entities

                    # Test loading second scene
                    frame_id_1 = FrameIdentifier(sequence_id=1, timestamp_monotime_us=float(timestamp_keys[1]))
                    scene_1 = provider.load_by_frame_id(frame_id_1)
                    assert scene_1 is not None
                    assert isinstance(scene_1, Scene)
                    assert len(scene_1.entities) == 1
                    assert EntityId("car_1") in scene_1.entities

                    # Test metadata
                    metadata = provider.get_metadata()
                    assert metadata.total_lines == 2
                    assert metadata.decoder_name == "test_decoder"
                    assert metadata.file_path == temp_file
                    assert isinstance(metadata.source_fingerprint, SourceFingerprint)
                    assert metadata.sequence_lookup_enabled is False
                    assert len(metadata.timestamp_to_sequence) == 2

                    # Clean up
                    provider.close()

        finally:
            # Clean up temp file
            Path(temp_file).unlink(missing_ok=True)

    def test_sequence_lookup_flag_controls_lookup_behavior(self) -> None:
        """Sequence lookup should only succeed when explicitly enabled."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00Z", "detections": []},
            {"timestamp": "2024-01-01T12:00:01Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for row in test_data:
                f.write(json.dumps(row) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    # Provider with sequence lookup disabled (default)
                    disabled_provider = _build_provider(
                        temp_file,
                        timestamp_fallback_policy=TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY),
                    )

                    # Build a FrameIdentifier with incorrect timestamp but valid sequence index
                    from ax_devil.core.data_types import FrameIdentifier

                    sequence_timestamp_us = max(disabled_provider.get_available_frames())
                    mismatched_identifier = FrameIdentifier(
                        sequence_id=1,
                        timestamp_monotime_us=float(sequence_timestamp_us + 1),
                    )
                    assert disabled_provider.load_by_frame_id(mismatched_identifier) is None
                    disabled_provider.close()

                    # Provider with sequence lookup enabled should recover via sequence id
                    enabled_provider = _build_provider(
                        temp_file,
                        supports_sequence_lookup=True,
                        timestamp_fallback_policy=TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY),
                    )

                    scene = enabled_provider.load_by_frame_id(mismatched_identifier)
                    assert isinstance(scene, Scene)
                    assert scene is not None
                    enabled_provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_timestamp_tolerance_matches_previous_overlay_timestamp(self) -> None:
        """Tolerance lookup should match the latest overlay timestamp at or before the request."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00.001000Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for row in test_data:
                f.write(json.dumps(row) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    provider = _build_provider(
                        temp_file,
                        timestamp_fallback_policy=TimestampFallbackPolicy(tolerance_us=5_000),
                    )

                    from ax_devil.core.data_types import FrameIdentifier

                    request_timestamp_us = max(provider.get_available_frames()) + 1_000
                    frame_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=float(request_timestamp_us))

                    result = provider.lookup_by_frame_id(frame_id)
                    assert result.scene is not None
                    assert result.to_overlay_metadata() == {
                        "requested_timestamp_us": request_timestamp_us,
                        "matched_timestamp_us": request_timestamp_us - 1_000,
                        "timestamp_match_type": "tolerated_past",
                        "timestamp_tolerance_us": 5_000,
                        "timestamp_effective_tolerance_us": 5_000,
                        "timestamp_fallback_mode": "previous_with_tolerance",
                        "overlay_alignment_basis": "timestamp",
                        "requested_sequence_id": 0,
                        "matched_sequence_id": 0,
                        "timestamp_offset_us": -1_000,
                    }
                    provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_exact_timestamp_fallback_mode_rejects_previous_timestamp_fallback(self) -> None:
        """Exact-only timestamp fallback should not use previous overlay timestamps."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00.001000Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for row in test_data:
                f.write(json.dumps(row) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    provider = _build_provider(
                        temp_file,
                        timestamp_fallback_policy=TimestampFallbackPolicy(
                            mode=TimestampFallbackMode.EXACT_ONLY,
                            tolerance_us=5_000,
                        ),
                    )

                    from ax_devil.core.data_types import FrameIdentifier

                    request_timestamp_us = max(provider.get_available_frames()) + 1_000
                    frame_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=float(request_timestamp_us))

                    result = provider.lookup_by_frame_id(frame_id)
                    assert result.scene is None
                    assert result.to_overlay_metadata() == {
                        "requested_timestamp_us": request_timestamp_us,
                        "matched_timestamp_us": None,
                        "timestamp_match_type": "missing",
                        "timestamp_tolerance_us": 5_000,
                        "timestamp_effective_tolerance_us": 0,
                        "timestamp_fallback_mode": "exact_only",
                        "overlay_alignment_basis": "timestamp",
                        "requested_sequence_id": 0,
                        "matched_sequence_id": None,
                    }
                    provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_exact_timestamp_fallback_mode_preserves_causal_sequence_lookup(self) -> None:
        """Exact-only timestamp fallback should not disable non-future sequence lookup."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00Z", "detections": []},
            {"timestamp": "2024-01-01T12:00:01Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for row in test_data:
                f.write(json.dumps(row) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    provider = _build_provider(
                        temp_file,
                        supports_sequence_lookup=True,
                        timestamp_fallback_policy=TimestampFallbackPolicy(
                            mode=TimestampFallbackMode.EXACT_ONLY,
                            tolerance_us=5_000,
                        ),
                    )

                    from ax_devil.core.data_types import FrameIdentifier

                    sequence_timestamp_us = max(provider.get_available_frames())
                    result = provider.lookup_by_frame_id(
                        FrameIdentifier(sequence_id=1, timestamp_monotime_us=float(sequence_timestamp_us + 1))
                    )

                    assert result.scene is not None
                    assert result.match_type == "sequence"
                    assert result.alignment_basis == "sequence"
                    assert result.requested_sequence_id == 1
                    assert result.matched_sequence_id == 1
                    assert result.timestamp_fallback_policy.mode is TimestampFallbackMode.EXACT_ONLY
                    provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_sequence_lookup_rejects_future_overlay_timestamp(self) -> None:
        """Sequence lookup should not display an overlay before its timestamp is reached."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for row in test_data:
                f.write(json.dumps(row) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    provider = _build_provider(
                        temp_file,
                        supports_sequence_lookup=True,
                        timestamp_fallback_policy=TimestampFallbackPolicy(tolerance_us=5_000),
                    )

                    from ax_devil.core.data_types import FrameIdentifier

                    request_timestamp_us = min(provider.get_available_frames()) - 1_000
                    frame_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=float(request_timestamp_us))

                    assert provider.load_by_frame_id(frame_id) is None
                    provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_timestamp_tolerance_rejects_later_overlay_even_inside_tolerance(self) -> None:
        """Tolerance lookup should reject future overlay timestamps even inside the tolerance window."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00.006000Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for row in test_data:
                f.write(json.dumps(row) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    provider = _build_provider(
                        temp_file,
                        timestamp_fallback_policy=TimestampFallbackPolicy(tolerance_us=5_000),
                    )

                    from ax_devil.core.data_types import FrameIdentifier

                    request_timestamp_us = min(provider.get_available_frames()) - 1_000
                    frame_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=float(request_timestamp_us))

                    assert provider.load_by_frame_id(frame_id) is None
                    provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_jsonl_provider_with_empty_lines(self) -> None:
        """Test JSONL provider handles empty lines correctly."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00Z", "detections": []},
            "",  # Empty line
            {"timestamp": "2024-01-01T12:00:01Z", "detections": []},
            "   ",  # Whitespace-only line
            {"timestamp": "2024-01-01T12:00:02Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for data in test_data:
                if isinstance(data, str):
                    f.write(data + "\n")
                else:
                    f.write(json.dumps(data) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    provider = _build_provider(temp_file)

                    # Should only count non-empty lines (3 valid JSON lines)
                    assert provider.get_total_frames() == 3
                    available_frames = provider.get_available_frames()
                    # Should have 3 timestamp keys instead of line numbers
                    assert len(available_frames) == 3
                    # Check that all keys are positive integers (timestamp microseconds)
                    assert all(isinstance(key, int) and key > 0 for key in available_frames)

                    provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_jsonl_provider_with_invalid_json(self) -> None:
        """Test JSONL provider handles invalid JSON gracefully."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00Z", "detections": []},
            "invalid json line",
            {"timestamp": "2024-01-01T12:00:01Z", "detections": []},
            "{incomplete json",
            {"timestamp": "2024-01-01T12:00:02Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for data in test_data:
                if isinstance(data, str):
                    f.write(data + "\n")
                else:
                    f.write(json.dumps(data) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    provider = _build_provider(temp_file)

                    # Should only count valid JSON lines (3 valid JSON lines)
                    assert provider.get_total_frames() == 3
                    available_frames = provider.get_available_frames()
                    # Should have 3 timestamp keys instead of line numbers
                    assert len(available_frames) == 3
                    # Check that all keys are positive integers (timestamp microseconds)
                    assert all(isinstance(key, int) and key > 0 for key in available_frames)

                    provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_source_index_cold_restore(self) -> None:
        """A new provider must restore from the persisted source index without re-parsing."""
        test_data = [
            {
                "timestamp": "2024-01-01T12:00:00Z",
                "detections": [{"id": "a", "class": "person", "confidence": 0.9, "bbox": [0.1, 0.2, 0.3, 0.4]}],
            },
            {
                "timestamp": "2024-01-01T12:00:01Z",
                "detections": [{"id": "b", "class": "car", "confidence": 0.8, "bbox": [0.5, 0.6, 0.2, 0.3]}],
            },
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for data in test_data:
                f.write(json.dumps(data) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    # First provider builds and persists the source index
                    provider1 = _build_provider(temp_file)
                    original_frames = provider1.get_available_frames()
                    original_metadata = provider1.get_metadata()
                    provider1.close()

                    # Second provider should restore from persisted index, not rebuild
                    with patch.object(
                        type(provider1._store), "_rebuild", side_effect=AssertionError("rebuild must not be called")
                    ):
                        provider2 = _build_provider(temp_file)
                        assert provider2.get_available_frames() == original_frames
                        assert provider2.get_total_frames() == original_metadata.total_lines

                        # Scenes should still load correctly via seek+decode from source
                        from ax_devil.core.data_types import FrameIdentifier

                        for ts in original_frames:
                            frame_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=float(ts))
                            scene = provider2.load_by_frame_id(frame_id)
                            assert scene is not None
                            assert isinstance(scene, Scene)

                        provider2.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_source_index_decode_failure_returns_missing_scene(self) -> None:
        """A corrupt indexed source record should not crash frame presentation."""
        test_data = [
            {
                "timestamp": "2024-01-01T12:00:00Z",
                "detections": [{"id": "a", "class": "person", "confidence": 0.9, "bbox": [0.1, 0.2, 0.3, 0.4]}],
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for data in test_data:
                f.write(json.dumps(data) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    provider = _build_provider(temp_file)
                    timestamp_key = next(iter(provider.get_available_frames()))

                    Path(temp_file).write_text(
                        '{"timestamp":"2024-01-01T12:00:00Z","detections":[{"id":"broken',
                        encoding="utf-8",
                    )

                    from ax_devil.core.data_types import FrameIdentifier

                    frame_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=float(timestamp_key))
                    assert provider.load_by_frame_id(frame_id) is None
                    provider.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_fingerprint_mismatch_triggers_rebuild(self) -> None:
        """Modifying the source file must invalidate the persisted index and trigger a rebuild."""
        test_data = [
            {"timestamp": "2024-01-01T12:00:00Z", "detections": []},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for data in test_data:
                f.write(json.dumps(data) + "\n")
            temp_file = f.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    # Build initial index
                    provider1 = _build_provider(temp_file)
                    assert provider1.get_total_frames() == 1
                    provider1.close()

                    # Modify the source file so its fingerprint changes
                    with open(temp_file, "a") as f:
                        f.write(json.dumps({"timestamp": "2024-01-01T12:00:01Z", "detections": []}) + "\n")

                    # New provider should detect mismatch and rebuild
                    provider2 = _build_provider(temp_file)
                    assert provider2.get_total_frames() == 2
                    assert len(provider2.get_available_frames()) == 2
                    provider2.close()

        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_source_index_requires_matching_artifact_identity(self) -> None:
        """Source indexes rebuild for changed options, legacy entries, and version bumps."""
        test_data = [{"timestamp": "2024-01-01T12:00:00Z", "detections": []}]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as file_obj:
            for data in test_data:
                file_obj.write(json.dumps(data) + "\n")
            temp_file = file_obj.name

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    initial_provider = _build_provider(temp_file, decode_options={"profile": "initial"})
                    initial_provider.close()

                    index_path = next(cache_path.glob("*.scene-index.json"))
                    changed_options_provider = _build_provider(temp_file, decode_options={"profile": "changed"})
                    changed_options_provider.close()
                    changed_payload: dict[str, Any] = json.loads(index_path.read_text(encoding="utf-8"))
                    assert changed_payload["metadata"]["artifact_identity"]["decode_options"] == {"profile": "changed"}

                    changed_payload["metadata"].pop("artifact_identity")
                    index_path.write_text(json.dumps(changed_payload), encoding="utf-8")
                    legacy_provider = _build_provider(temp_file, decode_options={"profile": "changed"})
                    legacy_provider.close()
                    rebuilt_payload: dict[str, Any] = json.loads(index_path.read_text(encoding="utf-8"))
                    assert "artifact_identity" in rebuilt_payload["metadata"]

                    versioned_provider = _build_provider(
                        temp_file,
                        artifact_version=2,
                        decode_options={"profile": "changed"},
                    )
                    versioned_provider.close()
                    versioned_payload: dict[str, Any] = json.loads(index_path.read_text(encoding="utf-8"))
                    assert versioned_payload["metadata"]["artifact_identity"]["artifact_version"] == 2
        finally:
            Path(temp_file).unlink(missing_ok=True)

    def test_derived_cache_requires_matching_artifact_identity(self) -> None:
        """Derived scenes are reused only for the same complete artifact identity."""
        test_data = [{"timestamp": "2024-01-01T12:00:00Z", "detections": []}]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as file_obj:
            for data in test_data:
                file_obj.write(json.dumps(data) + "\n")
            temp_file = file_obj.name

        builds: list[None] = []

        class CountingProvider(SceneDecoderFileProvider):
            """Record full-source builds while preserving the production provider behavior."""

            def _build_scene_maps(
                self,
                payloads: Iterable[str],
                decoder: PayloadToSceneDecoder,
            ) -> SceneBuildResult:
                builds.append(None)
                return super()._build_scene_maps(payloads, decoder)

        try:
            with tempfile.TemporaryDirectory() as cache_dir:
                cache_path = Path(cache_dir)
                with patch.object(CacheManager, "get_cache_subdir", return_value=cache_path):
                    cold_provider = _build_provider(
                        temp_file,
                        storage_mode=StorageMode.DERIVED_CACHE,
                        provider_type=CountingProvider,
                    )
                    cold_provider.close()
                    assert len(builds) == 1

                    warm_provider = _build_provider(
                        temp_file,
                        storage_mode=StorageMode.DERIVED_CACHE,
                        provider_type=CountingProvider,
                    )
                    warm_provider.close()
                    assert len(builds) == 1

                    cache_reader = IndexedFrameCache(temp_file, cache_type="test_decoder")
                    cached_metadata = cache_reader.load_metadata()
                    cached_frames = {
                        frame_id: cache_reader.load_frame(frame_id) for frame_id in cache_reader.available_frames()
                    }
                    cache_reader.close()
                    assert isinstance(cached_metadata, dict)
                    assert "artifact_identity" in cached_metadata

                    cached_metadata.pop("artifact_identity")
                    legacy_cache = IndexedFrameCache(temp_file, cache_type="test_decoder")
                    legacy_cache.save(frames=cached_frames, meta=cached_metadata)
                    legacy_cache.close()

                    legacy_provider = _build_provider(
                        temp_file,
                        storage_mode=StorageMode.DERIVED_CACHE,
                        provider_type=CountingProvider,
                    )
                    legacy_provider.close()
                    assert len(builds) == 2

                    versioned_provider = _build_provider(
                        temp_file,
                        storage_mode=StorageMode.DERIVED_CACHE,
                        artifact_version=2,
                        provider_type=CountingProvider,
                    )
                    versioned_provider.close()
                    assert len(builds) == 3
        finally:
            Path(temp_file).unlink(missing_ok=True)


def test_lookup_diagnostics_count_unique_timestamps_across_policy_changes(temp_dir: Path) -> None:
    """Repeated lookups are not calls, and changing fallback policy does not double-count a timestamp."""
    from ax_devil.core.data_types import FrameIdentifier
    from ax_devil.modules.diagnostics.metrics_store import get_metrics_store, set_metrics_enabled

    set_metrics_enabled(True)
    path = temp_dir / "diagnostics.jsonl"
    path.write_text(json.dumps({"timestamp": "2024-01-01T12:00:00Z", "detections": []}), encoding="utf-8")
    first = _build_provider(path)
    second = _build_provider(path)
    store = get_metrics_store()
    try:
        timestamp = next(iter(first.get_available_frames()))
        requested = FrameIdentifier(sequence_id=100, timestamp_monotime_us=timestamp + 5_000)
        first.lookup_by_frame_id(requested)
        first.lookup_by_frame_id(requested)
        metrics = store.snapshot()[first._metrics_id]
        assert metrics.observations["Unique requested timestamps"].value == 1
        assert metrics.observations["Unique missing timestamps"].value == 1
        first.set_timestamp_fallback_policy(TimestampFallbackPolicy(tolerance_us=10_000))
        first.lookup_by_frame_id(requested)
        metrics = store.snapshot()[first._metrics_id]
        assert metrics.observations["Unique requested timestamps"].value == 1
        assert metrics.observations["Unique fallback matches"].value == 1
        second.lookup_by_frame_id(requested)
        assert first._metrics_id != second._metrics_id
        first.close()
        assert first._metrics_id not in store.snapshot()
        assert second._metrics_id in store.snapshot()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("storage_mode", list(StorageMode))
def test_decoded_scene_working_set_reuses_and_reloads(storage_mode: StorageMode, tmp_path: Path) -> None:
    """Reuse two resolved scenes, preserve them across misses, and reload evicted history."""
    records = [
        {
            "timestamp": f"2024-01-01T12:00:0{index}Z",
            "detections": [
                {"id": f"entity-{index}", "class": "person", "confidence": 0.95, "bbox": [0.1, 0.2, 0.3, 0.4]}
            ],
        }
        for index in range(3)
    ]
    path = tmp_path / "scenes.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    with patch.object(CacheManager, "get_cache_subdir", return_value=tmp_path):
        provider = _build_provider(
            path,
            storage_mode=storage_mode,
            timestamp_fallback_policy=TimestampFallbackPolicy(tolerance_us=10_000),
        )
    timestamps = sorted(provider.get_available_frames())
    frames = [FrameIdentifier(sequence_id=index, timestamp_monotime_us=ts) for index, ts in enumerate(timestamps)]
    try:
        with patch.object(provider._store, "load_scene", wraps=provider._store.load_scene) as load:
            first = provider.load_by_frame_id(frames[0])
            second = provider.load_by_frame_id(frames[1])
            assert first is not None and second is not None
            assert provider.load_by_frame_id(frames[0]) is first
            assert provider.load_by_frame_id(frames[1]) is second

            missing = FrameIdentifier(sequence_id=99, timestamp_monotime_us=timestamps[-1] + 1_000_000)
            assert provider.load_by_frame_id(missing) is None
            fallback = FrameIdentifier(sequence_id=99, timestamp_monotime_us=timestamps[0] + 1_000)
            assert provider.load_by_frame_id(fallback) is first
            assert load.call_count == 2

            assert provider.load_by_frame_id(frames[2]) is not None
            assert provider.load_by_frame_id(frames[0]) is first
            reloaded = provider.load_by_frame_id(frames[1])
            assert reloaded is not second
            assert reloaded == second
            assert load.call_args_list == [call(timestamps[index]) for index in (0, 1, 2, 1)]
    finally:
        provider.close()
    assert all(provider._runtime_cache.get(timestamp) is None for timestamp in timestamps)


def test_sticky_selection_is_independent_of_navigation(tmp_path: Path) -> None:
    """Real indexed lookups retain original sample times across seeks and expiry."""
    from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
    from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings

    path = tmp_path / "sparse.jsonl"
    records = [
        {
            "timestamp": "2024-01-01T12:00:08Z",
            "detections": [{"id": "a", "class": "person", "confidence": 0.9, "bbox": [0.1, 0.2, 0.3, 0.4]}],
        },
        {"timestamp": "2024-01-01T12:00:10Z", "detections": []},
    ]
    path.write_text("".join(f"{json.dumps(record)}\n" for record in records))
    with patch.object(CacheManager, "get_cache_subdir", return_value=tmp_path):
        provider = _build_provider(path)
    source = FileOverlaySource(path, lambda _: provider, "test", timestamp_fallback_policy=TimestampFallbackPolicy())
    policy = OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=True, timeout_ms=2050, opacity=0.4))
    first, second = sorted(provider.get_available_frames())
    try:
        # Direct jump, exact empty update, backward step, and revisit all agree.
        for timestamp, expected in [
            (first + 1_970_000, first),
            (second, second),
            (second - 30_000, first),
            (first + 1_970_000, first),
        ]:
            selection = policy.select_from_source(source, FrameIdentifier(1, timestamp))
            assert selection.overlay is not None
            assert selection.overlay.frame_id.timestamp_monotime_us == expected
            assert selection.reused is (timestamp != expected)
            assert selection.effective_opacity == (0.4 if selection.reused else 1.0)
            assert bool(selection.overlay.content.entities) is (expected == first)
        assert policy.select_from_source(source, FrameIdentifier(0, first - 1)).overlay is None
        # Matching the same sample again must not restart its expiry clock.
        assert policy.select_from_source(source, FrameIdentifier(2, second + 40_000)).overlay is not None
        assert policy.select_from_source(source, FrameIdentifier(3, second + 2_050_000)).overlay is not None
        assert policy.select_from_source(source, FrameIdentifier(4, second + 2_050_001)).overlay is None
        # Explicitly empty updates also remain authoritative during retention.
        empty = policy.select_from_source(source, FrameIdentifier(3, second + 1_000_000))
        assert empty.overlay is not None and not empty.overlay.content.entities
        # Unlimited persistence still cannot look into the future.
        policy.update_settings(OverlayPersistenceSettings(enabled=True, timeout_ms=None))
        assert policy.select_from_source(source, FrameIdentifier(5, second + 99_000_000)).overlay is not None
        assert policy.select_from_source(source, FrameIdentifier(0, first - 1)).overlay is None
        # Exact matching can coexist with sticky retention; disabling sticky restores strict matching.
        source.set_timestamp_fallback_policy(TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY))
        assert policy.select_from_source(source, FrameIdentifier(5, second + 1_000_000)).reused
        policy.update_settings(OverlayPersistenceSettings(enabled=False))
        assert policy.select_from_source(source, FrameIdentifier(5, second + 1_000_000)).overlay is None
    finally:
        source.close()
