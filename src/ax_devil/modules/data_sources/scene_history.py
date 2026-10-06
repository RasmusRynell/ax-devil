"""Whole-file Scene history: every event and where every entity appears, placed on a video's frames.

Indexing records history per overlay sample (`SceneHistoryRecords`); `SceneHistory.place()` maps those samples onto
one video's frames.
"""

from __future__ import annotations

import sys
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.scene.model import Event, Scene

FramePosition = Callable[[int], int]
"""Map a sample timestamp to the video frame index that first shows it: below 0 before the video, at or past the
frame count after it."""


@dataclass(frozen=True, slots=True)
class SampleEvent:
    """One Scene event, keyed by the timestamp of the sample that carries it."""

    timestamp_key: int
    kind: str
    label: str
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SampleTrack:
    """Where one entity appears, as runs of consecutive samples given by their first and last sample timestamps."""

    entity_id: str
    types: tuple[str, ...]  # Classification types in order of first appearance.
    runs: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class SceneHistoryRecords:
    """Sample-keyed events and entity appearances persisted with a Scene file index."""

    events: tuple[SampleEvent, ...]
    tracks: tuple[SampleTrack, ...]

    def to_metadata(self) -> dict[str, Any]:
        """Return the JSON-compatible form persisted with the index."""
        return {
            "events": [[event.timestamp_key, event.kind, event.label, list(event.entity_ids)] for event in self.events],
            "tracks": [
                [track.entity_id, list(track.types), [list(run) for run in track.runs]] for track in self.tracks
            ],
        }

    @classmethod
    def from_metadata(cls, raw: Any) -> SceneHistoryRecords | None:
        """Return persisted records, or None when they are missing or malformed."""
        try:
            events = tuple(
                SampleEvent(
                    _int(key), _str(kind), _str(label), tuple(_str(entity_id) for entity_id in _list(entity_ids))
                )
                for key, kind, label, entity_ids in _list(raw["events"])
            )
            tracks = tuple(
                SampleTrack(
                    _str(entity_id),
                    tuple(_str(object_type) for object_type in _list(types)),
                    tuple((_int(first), _int(last)) for first, last in _list(runs)),
                )
                for entity_id, types, runs in _list(raw["tracks"])
            )
        except (KeyError, TypeError, ValueError):
            return None
        return cls(events, tracks)


class SceneHistoryCollector:
    """Collect history records from decoded samples.

    A later sample with the same timestamp replaces an earlier one, matching which sample lookup serves.
    """

    def __init__(self) -> None:
        self._events: dict[int, tuple[SampleEvent, ...]] = {}
        self._entities: dict[int, tuple[tuple[str, tuple[str, ...]], ...]] = {}  # (entity id, types) per sample.
        self._types: dict[tuple[str, ...], tuple[str, ...]] = {}  # Shares equal type tuples across samples.

    def add(self, timestamp_key: int, scene: Scene) -> None:
        """Record the events and entities of one sample."""
        self._events[timestamp_key] = tuple(
            SampleEvent(timestamp_key, event.kind, event.label, _involved_ids(event)) for event in scene.events
        )
        entities: list[tuple[str, tuple[str, ...]]] = []
        for entity_id, entity in scene.entities.items():
            observation = entity.latest_observation
            types = tuple(c.type for c in observation.classification) if observation is not None else ()
            entities.append((sys.intern(str(entity_id)), self._types.setdefault(types, types)))
        self._entities[timestamp_key] = tuple(entities)

    def records(self) -> SceneHistoryRecords:
        """Return events in sample order and each entity's runs of consecutive samples."""
        keys = sorted(self._entities)
        runs: dict[str, list[list[int]]] = {}
        types: dict[str, dict[str, None]] = {}  # Insertion order is time order.
        last_position: dict[str, int] = {}
        for position, key in enumerate(keys):
            for entity_id, entity_types in self._entities[key]:
                types.setdefault(entity_id, {}).update(dict.fromkeys(entity_types))
                entity_runs = runs.setdefault(entity_id, [])
                if last_position.get(entity_id) == position - 1:
                    entity_runs[-1][1] = key
                else:
                    entity_runs.append([key, key])
                last_position[entity_id] = position
        return SceneHistoryRecords(
            events=tuple(event for key in keys for event in self._events[key]),
            tracks=tuple(
                SampleTrack(
                    entity_id,
                    tuple(types[entity_id]),
                    tuple((first, last) for first, last in entity_runs),
                )
                for entity_id, entity_runs in runs.items()
            ),
        )


@dataclass(frozen=True, slots=True)
class FrameEvent:
    """A Scene event placed on the video frame where it first takes effect."""

    frame_id: FrameIdentifier
    kind: str
    label: str
    entity_ids: tuple[str, ...]

    @classmethod
    def of(cls, frame_id: FrameIdentifier, event: Event) -> FrameEvent:
        """Place a Scene event on *frame_id*."""
        return cls(frame_id, event.kind, event.label, _involved_ids(event))


FrameSpan = tuple[int, int]
"""Consecutive video frames showing an entity: the indices of the first and last of them."""


@dataclass(frozen=True, slots=True)
class ObjectHistory:
    """Where one entity appears in the video.

    Spans are plain tuples of frame indices, which the garbage collector stops tracking; a file can hold thousands
    of objects, and every tracked object lengthens the full collections that pause the shared GUI thread.
    """

    entity_id: str
    types: tuple[str, ...]
    spans: tuple[FrameSpan, ...]
    frame_times_us: tuple[int, ...] = field(repr=False, compare=False)

    @property
    def first_seen(self) -> FrameIdentifier:
        """Return the first frame showing the entity."""
        return self._frame(self.spans[0][0])

    @property
    def last_seen(self) -> FrameIdentifier:
        """Return the last frame showing the entity."""
        return self._frame(self.spans[-1][1])

    def is_visible_at(self, frame: int) -> bool:
        """Return whether a span covers *frame*."""
        index = bisect_right(self.spans, frame, key=lambda span: span[0]) - 1
        return index >= 0 and frame <= self.spans[index][1]

    def _frame(self, index: int) -> FrameIdentifier:
        return FrameIdentifier(sequence_id=index, timestamp_monotime_us=self.frame_times_us[index])


class SceneHistory:
    """Every event and every entity's appearances in one overlay source, placed on one video's frames."""

    def __init__(
        self,
        *,
        events: tuple[FrameEvent, ...],
        objects: tuple[ObjectHistory, ...],
        frame_count: int,
    ) -> None:
        self.events = events
        self.objects = objects  # Ordered by first appearance.
        self.frame_count = frame_count
        self._objects_by_id = {history.entity_id: history for history in objects}
        self._events_by_id: dict[str, list[FrameEvent]] = {}
        for event in events:
            for entity_id in event.entity_ids:
                self._events_by_id.setdefault(entity_id, []).append(event)

    @classmethod
    def place(
        cls,
        records: SceneHistoryRecords,
        frame_times_us: tuple[int, ...],
        frame_position: FramePosition,
        shown_keys: Sequence[int | None],
    ) -> SceneHistory:
        """Place sample-keyed records on video frames.

        *frame_position* places events; events outside the video are omitted. *shown_keys* gives, per frame, the
        timestamp of the sample lookup shows there (None for no sample): an object spans exactly the frames showing
        one of its samples. Lookup never shows an older sample on a later frame, so each run of samples maps to one
        slice of frames, split where frames show no sample.
        """
        frame_count = len(frame_times_us)
        shown_frames = [frame for frame, key in enumerate(shown_keys) if key is not None]
        shown_frame_keys = [key for key in shown_keys if key is not None]
        empty_frames = [frame for frame, key in enumerate(shown_keys) if key is None]

        def frame_id(index: int) -> FrameIdentifier:
            return FrameIdentifier(sequence_id=index, timestamp_monotime_us=frame_times_us[index])

        events: list[FrameEvent] = []
        for sample_event in records.events:
            position = frame_position(sample_event.timestamp_key)
            if 0 <= position < frame_count:
                events.append(
                    FrameEvent(frame_id(position), sample_event.kind, sample_event.label, sample_event.entity_ids)
                )

        objects: list[ObjectHistory] = []
        for track in records.tracks:
            spans: list[FrameSpan] = []
            for first_key, last_key in track.runs:
                start = bisect_left(shown_frame_keys, first_key)
                stop = bisect_right(shown_frame_keys, last_key)
                if start < stop:
                    _add_spans(spans, shown_frames[start], shown_frames[stop - 1], empty_frames)
            if spans:
                objects.append(ObjectHistory(track.entity_id, track.types, tuple(spans), frame_times_us))
        objects.sort(key=lambda history: (history.spans[0][0], history.entity_id))
        return cls(events=tuple(events), objects=tuple(objects), frame_count=frame_count)

    def object(self, entity_id: str) -> ObjectHistory | None:
        """Return the history of *entity_id*, or None when it is never shown in the video."""
        return self._objects_by_id.get(entity_id)

    def events_involving(self, entity_id: str) -> tuple[FrameEvent, ...]:
        """Return the events involving *entity_id* in frame order."""
        return tuple(self._events_by_id.get(entity_id, ()))


def _involved_ids(event: Event) -> tuple[str, ...]:
    return tuple(str(entity_id) for entity_id in event.involved_entity_ids)


def _add_spans(spans: list[FrameSpan], first: int, last: int, empty_frames: list[int]) -> None:
    """Append the frames first..last, split around frames showing no sample and merged with a touching span."""
    start = first
    for empty in empty_frames[bisect_right(empty_frames, first) : bisect_left(empty_frames, last)]:
        if empty > start:
            _append_span(spans, (start, empty - 1))
        start = empty + 1
    _append_span(spans, (start, last))


def _append_span(spans: list[FrameSpan], span: FrameSpan) -> None:
    if spans and spans[-1][1] + 1 == span[0]:
        spans[-1] = (spans[-1][0], span[1])
    else:
        spans.append(span)


def _int(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Expected int, got {type(value).__name__}")
    return value


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Expected str, got {type(value).__name__}")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"Expected list, got {type(value).__name__}")
    return value
