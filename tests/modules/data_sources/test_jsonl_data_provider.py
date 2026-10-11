"""Tests for the JSONL scene file provider."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy
from tests.helpers.jsonl import write_jsonl

pytestmark = pytest.mark.usefixtures("isolated_cache")


def simple_test_decoder(json_string: str) -> Scene:
    """Decode one ``{"timestamp", "detections": [{"id", "class", "confidence", "bbox"}]}`` record."""
    data = json.loads(json_string)
    timestamp = datetime.fromisoformat(data.get("timestamp", "2024-01-01T12:00:00Z").replace("Z", "+00:00"))

    entities = {}
    for detection in data.get("detections", []):
        entity_id = EntityId(detection["id"])
        bbox = BoundingBox.from_xywh(*detection["bbox"])
        classification = Classification(type=detection["class"], score=Score(detection["confidence"]))
        observation = Observation(timestamp=timestamp, geometry=bbox, classification=[classification])
        entities[entity_id] = Entity(id=entity_id, observations=[observation])

    return Scene(time_slice=TimeSlice(start=timestamp, end=timestamp), entities=entities)


def _build_provider(
    file_path: str | Path,
    *,
    supports_sequence_lookup: bool = False,
    storage_mode: StorageMode = StorageMode.SOURCE_INDEX,
    artifact_version: int = 1,
    decode_options: dict[str, Any] | None = None,
    timestamp_fallback_policy: TimestampFallbackPolicy = TimestampFallbackPolicy(
        tolerance_us=0,
    ),
    decoded: list[str] | None = None,
) -> SceneDecoderFileProvider:
    """Build a provider; *decoded* collects every payload the decoder parses."""

    class _TestDecoder(PayloadToSceneDecoder):
        def decode(self, payload: Any) -> Scene:
            if decoded is not None:
                decoded.append(payload)
            return simple_test_decoder(payload)

    return SceneDecoderFileProvider(
        file_path=file_path,
        decoder_factory=_TestDecoder,
        decoder_name="test_decoder",
        artifact_version=artifact_version,
        decode_options=decode_options,
        supports_sequence_lookup=supports_sequence_lookup,
        storage_mode=storage_mode,
        timestamp_fallback_policy=timestamp_fallback_policy,
    )


def _record(second: int, *entity_ids: str) -> dict[str, Any]:
    return {
        "timestamp": f"2024-01-01T12:00:{second:02d}Z",
        "detections": [
            {"id": entity_id, "class": "person", "confidence": 0.9, "bbox": [0.1, 0.2, 0.3, 0.4]}
            for entity_id in entity_ids
        ],
    }


def _frame(timestamp_us: int, sequence_id: int = 0) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=sequence_id, timestamp_monotime_us=float(timestamp_us))


def test_records_are_indexed_by_timestamp_and_load_their_scenes(tmp_path: Path) -> None:
    """Each record is reachable at its own timestamp and decodes to its own objects."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0, "person_1"), _record(1, "car_1")])
    provider = _build_provider(path)
    try:
        first, second = sorted(provider.get_available_frames())
        assert second - first == 1_000_000
        first_scene = provider.load_by_frame_id(_frame(first))
        second_scene = provider.load_by_frame_id(_frame(second, 1))
        assert first_scene is not None and set(first_scene.entities) == {EntityId("person_1")}
        assert second_scene is not None and set(second_scene.entities) == {EntityId("car_1")}
    finally:
        provider.close()


def test_blank_and_invalid_lines_are_skipped(tmp_path: Path) -> None:
    """Blank, whitespace-only, and malformed lines do not hide the valid records around them."""
    path = write_jsonl(
        tmp_path / "messy.jsonl",
        [_record(0), "", "invalid json line", _record(1), "   ", "{incomplete json", _record(2)],
    )
    provider = _build_provider(path)
    try:
        timestamps = sorted(provider.get_available_frames())
        assert provider.get_total_frames() == 3
        assert [later - earlier for earlier, later in zip(timestamps, timestamps[1:])] == [1_000_000, 1_000_000]
    finally:
        provider.close()


def test_sequence_lookup_recovers_a_mismatched_timestamp_only_when_enabled(tmp_path: Path) -> None:
    """Exact-only timestamp matching still allows the opt-in, non-future sequence lookup."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0), _record(1)])
    exact_only = TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY, tolerance_us=5_000)
    disabled = _build_provider(path, timestamp_fallback_policy=exact_only)
    enabled = _build_provider(path, supports_sequence_lookup=True, timestamp_fallback_policy=exact_only)
    try:
        mismatched = _frame(max(disabled.get_available_frames()) + 1, sequence_id=1)
        assert disabled.load_by_frame_id(mismatched) is None
        result = enabled.lookup_by_frame_id(mismatched)
        assert result.scene is not None
        assert result.match_type == "sequence"
        assert result.matched_sequence_id == 1
    finally:
        disabled.close()
        enabled.close()


@pytest.mark.parametrize(
    ("policy", "expected_match"),
    [
        (TimestampFallbackPolicy(tolerance_us=5_000), "tolerated_past"),
        (TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY, tolerance_us=5_000), "missing"),
    ],
    ids=["previous-with-tolerance", "exact-only"],
)
def test_earlier_overlay_is_used_only_when_the_policy_allows_it(
    tmp_path: Path, policy: TimestampFallbackPolicy, expected_match: str
) -> None:
    """A request 1 ms after the only overlay matches it within tolerance, but never under exact-only."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0)])
    provider = _build_provider(path, timestamp_fallback_policy=policy)
    try:
        overlay_timestamp = max(provider.get_available_frames())
        result = provider.lookup_by_frame_id(_frame(overlay_timestamp + 1_000))
        assert result.match_type == expected_match
        if expected_match == "missing":
            assert result.scene is None
        else:
            assert result.scene is not None
            assert result.matched_timestamp_us == overlay_timestamp
            assert result.offset_us == -1_000
    finally:
        provider.close()


@pytest.mark.parametrize("supports_sequence_lookup", [False, True], ids=["timestamp", "sequence"])
def test_overlay_is_never_shown_before_its_timestamp(tmp_path: Path, supports_sequence_lookup: bool) -> None:
    """Neither tolerance nor a matching sequence id shows an overlay 1 ms before it is due."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0)])
    provider = _build_provider(
        path,
        supports_sequence_lookup=supports_sequence_lookup,
        timestamp_fallback_policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )
    try:
        assert provider.load_by_frame_id(_frame(min(provider.get_available_frames()) - 1_000)) is None
    finally:
        provider.close()


def test_source_index_decode_failure_returns_missing_scene(tmp_path: Path) -> None:
    """A corrupt indexed source record should not crash frame presentation."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0, "a")])
    provider = _build_provider(path)
    try:
        timestamp_key = next(iter(provider.get_available_frames()))
        path.write_text('{"timestamp":"2024-01-01T12:00:00Z","detections":[{"id":"broken', encoding="utf-8")
        assert provider.load_by_frame_id(_frame(timestamp_key)) is None
    finally:
        provider.close()


@pytest.mark.parametrize("storage_mode", list(StorageMode))
def test_persisted_scenes_are_reused_only_for_an_unchanged_source_and_identity(
    tmp_path: Path, storage_mode: StorageMode
) -> None:
    """Reopening reuses the persisted artifact; changed options, version, or source contents rebuild it."""
    path = write_jsonl(tmp_path / "scenes.jsonl", [_record(0, "a"), _record(1, "b")])
    options = {"profile": "initial"}

    def open_provider(decoded: list[str], **kwargs: Any) -> SceneDecoderFileProvider:
        provider = _build_provider(path, storage_mode=storage_mode, decoded=decoded, **kwargs)
        provider.close()
        return provider

    cold: list[str] = []
    cold_frames = open_provider(cold, decode_options=options).get_available_frames()
    assert len(cold) == 2

    warm: list[str] = []
    warm_provider = _build_provider(path, storage_mode=storage_mode, decoded=warm, decode_options=options)
    try:
        assert warm == []
        assert warm_provider.get_available_frames() == cold_frames
        assert all(warm_provider.load_by_frame_id(_frame(timestamp)) is not None for timestamp in cold_frames)
    finally:
        warm_provider.close()

    changed_options: list[str] = []
    open_provider(changed_options, decode_options={"profile": "changed"})
    assert len(changed_options) == 2

    new_version: list[str] = []
    open_provider(new_version, decode_options={"profile": "changed"}, artifact_version=2)
    assert len(new_version) == 2

    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(f"{json.dumps(_record(2))}\n")
    changed_source: list[str] = []
    rebuilt = open_provider(changed_source, decode_options={"profile": "changed"}, artifact_version=2)
    assert len(changed_source) == 3
    assert rebuilt.get_total_frames() == 3


def test_lookup_diagnostics_count_unique_timestamps_across_policy_changes(temp_dir: Path) -> None:
    """Repeated lookups are not calls, and changing fallback policy does not double-count a timestamp."""
    from ax_devil.modules.diagnostics.metrics_store import get_metrics_store, set_metrics_enabled, source_identity

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
        metrics = store.snapshot()[source_identity(first)]
        assert metrics.observations["Unique requested timestamps"].value == 1
        assert metrics.observations["Unique missing timestamps"].value == 1
        first.set_timestamp_fallback_policy(TimestampFallbackPolicy(tolerance_us=10_000))
        first.lookup_by_frame_id(requested)
        metrics = store.snapshot()[source_identity(first)]
        assert metrics.observations["Unique requested timestamps"].value == 1
        assert metrics.observations["Unique fallback matches"].value == 1
        second.lookup_by_frame_id(requested)
        assert source_identity(first) != source_identity(second)
        first.close()
        assert source_identity(first) not in store.snapshot()
        assert source_identity(second) in store.snapshot()
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
            assert load.call_count == 4
    finally:
        provider.close()


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
