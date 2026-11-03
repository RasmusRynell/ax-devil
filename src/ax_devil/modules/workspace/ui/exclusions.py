"""Consideration items the user excluded during this session."""

from __future__ import annotations

from ax_devil.modules.workspace.core import ConsiderationItemRef, ItemResolution


class Exclusions:
    """Consideration items excluded this session, by ref; never part of the Workspace.

    They name entries, so they stay with the same ones when an item resolves again: an entry that is gone loses its
    exclusion and a new one starts at its default.
    """

    def __init__(self) -> None:
        self._excluded: set[ConsiderationItemRef] = set()

    def is_considered(self, ref: ConsiderationItemRef) -> bool:
        """Return whether *ref* participates in navigation and layout."""
        return ref not in self._excluded

    def set_considered(self, ref: ConsiderationItemRef, considered: bool) -> None:
        """Include *ref* again, or exclude it."""
        if considered:
            self._excluded.discard(ref)
        else:
            self._excluded.add(ref)

    def remove_item(self, item_id: str) -> None:
        """Drop every exclusion that names an entry of the item with *item_id*."""
        self._excluded = {ref for ref in self._excluded if ref.item_id != item_id}

    def follow(self, previous: ItemResolution, current: ItemResolution) -> None:
        """Carry exclusions from *previous* to *current*: drop those of gone entries, exclude new non-default ones."""
        before, after = _defaults(previous), _defaults(current)
        self._excluded = {ref for ref in self._excluded if ref not in before or ref in after} | {
            ref for ref, considered in after.items() if ref not in before and not considered
        }


def _defaults(resolution: ItemResolution) -> dict[ConsiderationItemRef, bool]:
    """Return whether each consideration item of *resolution*'s Content is considered by default, by its ref."""
    return {
        consideration.ref: consideration.default_considered
        for content in resolution.contents
        for consideration in content.consideration_items()
    }
