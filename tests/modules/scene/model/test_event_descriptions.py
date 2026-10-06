"""Tests for how Scene events describe themselves to event lists."""

from __future__ import annotations

from ax_devil.modules.scene.model import Delete, EntityId, Event, Merge, Rename, Split, TimeSlice

_AT = TimeSlice(start=0, end=0)


def test_operations_describe_themselves() -> None:
    events: list[Event] = [
        Delete(timestamp=_AT, entity_id=EntityId("12")),
        Rename(timestamp=_AT, from_entity_id=EntityId("12"), to_entity_id=EntityId("57")),
        Merge(timestamp=_AT, entity_ids=[EntityId("1"), EntityId("2")], target_entity_id=EntityId("3")),
        Split(timestamp=_AT, source_entity_id=EntityId("3"), new_entity_ids=[EntityId("4"), EntityId("5")]),
    ]

    assert [event.label for event in events] == [
        "Delete 12",
        "Rename 12 → 57",
        "Merge 1, 2 → 3",
        "Split 3 → 4, 5",
    ]
    assert [event.kind for event in events] == ["Delete", "Rename", "Merge", "Split"]
    assert [event.involved_entity_ids for event in events] == [
        ("12",),
        ("12", "57"),
        ("1", "2", "3"),
        ("3", "4", "5"),
    ]
