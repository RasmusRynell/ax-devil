"""Tests for indexing Scene history and placing it on video frames."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.cache import CacheManager
from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.data_sources.scene_history import (
    FrameEvent,
    SampleEvent,
    SampleTrack,
    SceneHistory,
    SceneHistoryCollector,
    SceneHistoryRecords,
)
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import (
    Delete,
    EntityId,
    Rename,
    Scene,
    TimeSlice,
)
from tests.helpers.entities import entity_with_classes
from tests.helpers.jsonl import write_jsonl

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_VIDEO = FrameTimeline.from_timestamps([0, 40_000, 80_000, 120_000])


class _HistoryDecoder(PayloadToSceneDecoder):
    """Decode ``{"t": seconds | frame, "seen": [...], "delete": [...], "rename": [[from, to]]}`` records."""

    def decode(self, payload: Any) -> Scene:
        record = json.loads(payload)
        start: datetime | int = record["t"] if record.get("frame_keyed") else _EPOCH + timedelta(seconds=record["t"])
        time_slice = TimeSlice(start=start, end=start)
        scene = Scene(time_slice=time_slice)
        for entity_id in record.get("seen", []):
            scene.add_entity(entity_with_classes("human", entity_id=entity_id))
        for entity_id in record.get("delete", []):
            scene.add_event(Delete(timestamp=time_slice, entity_id=EntityId(entity_id)))
        for from_id, to_id in record.get("rename", []):
            scene.add_event(
                Rename(timestamp=time_slice, from_entity_id=EntityId(from_id), to_entity_id=EntityId(to_id))
            )
        return scene


class _CountingDecoder(_HistoryDecoder):
    """Count decoded records so tests can tell a cache restore from a rebuild."""

    decoded = 0

    def decode(self, payload: Any) -> Scene:
        _CountingDecoder.decoded += 1
        return super().decode(payload)


def _provider(
    path: Path,
    cache_dir: Path,
    *,
    storage_mode: StorageMode = StorageMode.SOURCE_INDEX,
    supports_sequence_lookup: bool = False,
) -> SceneDecoderFileProvider:
    with patch.object(CacheManager, "get_cache_subdir", return_value=cache_dir):
        return SceneDecoderFileProvider(
            path,
            decoder_factory=_CountingDecoder,
            decoder_name="history_test",
            artifact_version=1,
            storage_mode=storage_mode,
            supports_sequence_lookup=supports_sequence_lookup,
        )


def _frame(index: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=index, timestamp_monotime_us=_VIDEO.timestamps_us[index])


def test_collector_records_runs_of_consecutive_samples_and_types_in_first_seen_order() -> None:
    collector = SceneHistoryCollector()
    for key, entities in [(30, ("a",)), (10, ("a", "b")), (20, ("b",)), (40, ("a",))]:
        scene = Scene(time_slice=TimeSlice(start=key, end=key))
        for entity_id in entities:
            scene.add_entity(entity_with_classes("car" if key == 30 else "human", entity_id=entity_id))
        collector.add(key, scene)

    records = collector.records()

    assert {track.entity_id: track for track in records.tracks} == {
        "a": SampleTrack("a", ("human", "car"), ((10, 10), (30, 40))),
        "b": SampleTrack("b", ("human",), ((10, 20),)),
    }


def test_collector_keeps_only_the_served_sample_for_a_repeated_timestamp() -> None:
    """A later sample at the same timestamp replaces the earlier one's objects, types, and events."""
    collector = SceneHistoryCollector()
    old = Scene(time_slice=TimeSlice(start=5, end=5))
    old.add_entity(entity_with_classes("human", entity_id="old"))
    old.add_entity(entity_with_classes("human", entity_id="a"))
    old.add_event(Delete(timestamp=old.time_slice, entity_id=EntityId("old")))
    new = Scene(time_slice=TimeSlice(start=5, end=5))
    new.add_entity(entity_with_classes("car", entity_id="a"))
    new.add_event(Delete(timestamp=new.time_slice, entity_id=EntityId("new")))
    collector.add(5, old)
    collector.add(5, new)

    assert collector.records() == SceneHistoryRecords(
        events=(SampleEvent(5, "Delete", "Delete new", ("new",)),),
        tracks=(SampleTrack("a", ("car",), ((5, 5),)),),
    )


def test_records_round_trip_and_reject_malformed_metadata() -> None:
    records = SceneHistoryRecords(
        events=(SampleEvent(1, "Rename", "Rename a → b", ("a", "b")),),
        tracks=(SampleTrack("a", ("human",), ((0, 1), (3, 4))),),
    )

    assert SceneHistoryRecords.from_metadata(json.loads(json.dumps(records.to_metadata()))) == records
    assert SceneHistoryRecords.from_metadata(None) is None
    assert SceneHistoryRecords.from_metadata({"events": [], "tracks": [["a", ["human"], [[0, "1"]]]]}) is None
    assert SceneHistoryRecords.from_metadata({"events": [[True, "x", []]], "tracks": []}) is None


def test_placement_follows_the_sample_each_frame_shows() -> None:
    records = SceneHistoryRecords(
        events=(
            SampleEvent(-10, "Delete", "Delete before", ("before",)),
            SampleEvent(50_000, "Rename", "Rename a → b", ("a", "b")),
            SampleEvent(130_000, "Delete", "Delete after", ("b",)),
        ),
        tracks=(
            SampleTrack("b", ("car",), ((50_000, 60_000), (110_000, 200_000))),
            SampleTrack("a", ("human",), ((-20_000, 40_000),)),
            SampleTrack("split", ("human",), ((0, 200_000),)),
            SampleTrack("touching", ("human",), ((0, 0), (40_000, 40_000))),
            SampleTrack("never shown", ("human",), ((50_000, 60_000),)),
        ),
    )
    times = _VIDEO.timestamps_us

    def position(key: int) -> int:
        from bisect import bisect_left

        return -1 if key < times[0] else bisect_left(times, key)

    history = SceneHistory.place(records, times, position, [0, 40_000, None, 110_000])

    assert history.events == (FrameEvent(_frame(2), "Rename", "Rename a → b", ("a", "b")),)
    assert [(h.entity_id, h.spans) for h in history.objects] == [
        ("a", ((0, 1),)),
        ("split", ((0, 1), (3, 3))),
        ("touching", ((0, 1),)),
        ("b", ((3, 3),)),
    ]
    b = history.objects[3]
    assert (b.first_seen, b.last_seen) == (_frame(3), _frame(3))
    assert [b.is_visible_at(frame) for frame in range(4)] == [False, False, False, True]
    assert history.object("never shown") is None
    assert history.events_involving("a") == history.events_involving("b")[:1] == history.events
    assert history.events_involving("before") == ()


def test_objects_follow_retention_and_replacement_like_lookup(tmp_path: Path) -> None:
    """Retained samples keep objects shown; a replacing sample hides them even between video frames."""
    path = write_jsonl(
        tmp_path / "lookup.jsonl",
        [
            {"t": 0.0, "seen": ["kept", "replaced"]},
            {"t": 0.01, "seen": ["kept", "replaced"]},
            {"t": 0.02, "seen": ["kept"]},
        ],
    )
    provider = _provider(path, tmp_path)
    try:
        sticky = provider.scene_history(_VIDEO, allow_previous=True, max_sample_age_us=None)
        aged = provider.scene_history(_VIDEO, allow_previous=True, max_sample_age_us=70_000)
        not_sticky = provider.scene_history(_VIDEO, allow_previous=False, max_sample_age_us=None)
    finally:
        provider.close()

    assert [(h.entity_id, h.spans) for h in sticky.objects] == [("kept", ((0, 3),)), ("replaced", ((0, 0),))]
    assert [(h.entity_id, h.spans) for h in aged.objects] == [("kept", ((0, 2),)), ("replaced", ((0, 0),))]
    # Without sticky overlays, only the 50 ms fallback tolerance lets frame 1 show the 20 ms sample.
    assert [(h.entity_id, h.spans) for h in not_sticky.objects] == [("kept", ((0, 1),)), ("replaced", ((0, 0),))]


@pytest.mark.parametrize("storage_mode", list(StorageMode))
def test_provider_history_survives_a_cache_restore(storage_mode: StorageMode, tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "history.jsonl",
        [
            {"t": 0.08, "seen": ["b"], "delete": ["a"]},
            {"t": 0.0, "seen": ["a"], "rename": [["x", "a"]]},
            {"t": 0.04, "seen": ["a", "b"]},
        ],
    )
    cold = _provider(path, tmp_path, storage_mode=storage_mode)
    try:
        expected = cold.scene_history(_VIDEO, allow_previous=True, max_sample_age_us=None)
    finally:
        cold.close()
    assert [(event.frame_id.sequence_id, event.label) for event in expected.events] == [
        (0, "Rename x → a"),
        (2, "Delete a"),
    ]
    assert [(h.entity_id, h.first_seen.sequence_id, h.last_seen.sequence_id) for h in expected.objects] == [
        ("a", 0, 1),
        ("b", 1, 3),  # The last sample stays shown until the video ends.
    ]

    _CountingDecoder.decoded = 0
    warm = _provider(path, tmp_path, storage_mode=storage_mode)
    try:
        restored = warm.scene_history(_VIDEO, allow_previous=True, max_sample_age_us=None)
        assert _CountingDecoder.decoded == 0
        assert restored.events == expected.events
        assert restored.objects == expected.objects
    finally:
        warm.close()


def test_frame_keyed_samples_land_on_their_sequence_frame(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "frames.jsonl",
        [
            {"t": 2, "frame_keyed": True, "seen": ["a"], "delete": ["a"]},
            {"t": 4, "frame_keyed": True, "delete": ["beyond"]},
        ],
    )
    provider = _provider(path, tmp_path, supports_sequence_lookup=True)
    try:
        history = provider.scene_history(_VIDEO, allow_previous=True, max_sample_age_us=None)
        exact = provider.scene_history(_VIDEO, allow_previous=False, max_sample_age_us=None)
    finally:
        provider.close()
    assert history.events == (FrameEvent(_frame(2), "Delete", "Delete a", ("a",)),)
    assert history.objects[0].spans == ((2, 3),)  # Retained until the video ends.
    assert exact.objects[0].spans == ((2, 2),)


def test_source_index_without_history_is_rebuilt(tmp_path: Path) -> None:
    """Indexes written before history was collected are rebuilt instead of showing an empty history."""
    path = write_jsonl(tmp_path / "legacy.jsonl", [{"t": 0.0, "seen": ["a"]}])
    _provider(path, tmp_path).close()
    (index_path,) = tmp_path.rglob("*.scene-index.json")
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    del payload["metadata"]["history"]
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    provider = _provider(path, tmp_path)
    try:
        history = provider.scene_history(_VIDEO, allow_previous=True, max_sample_age_us=None)
        assert [h.entity_id for h in history.objects] == ["a"]
    finally:
        provider.close()
