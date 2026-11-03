"""Shared helpers for constructing filter predicates.

These utilities remain lightweight on purpose; each data producer still
declares its own class lists while reusing the common predicate wiring.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ax_devil.modules.filtering.filter_config import FilterOption, FilterState
from ax_devil.modules.scene.decoding import UNMAPPED_CLASS, is_unmapped_class
from ax_devil.modules.scene.model import Entity


@dataclass(slots=True, frozen=True)
class ClassificationFilter:
    """Classification policy shared by real entities and recorded history types."""

    types: frozenset[str]
    include_empty: bool = False
    exclude: bool = False

    def matches_types(self, types: Sequence[str]) -> bool:
        """Match recorded types; an empty sequence represents an unclassified object."""
        if not types:
            return self.include_empty
        return any(self._matches_type(classification_type) for classification_type in types)

    def _matches_type(self, classification_type: str) -> bool:
        if not classification_type:
            return False
        listed = classification_type in self.types
        key = classification_type if listed or not is_unmapped_class(classification_type) else UNMAPPED_CLASS
        return key not in self.types if self.exclude else key in self.types

    def __call__(self, entity: Entity, _: FilterState) -> bool:
        """Match any observation's classifications without inspecting other entity data."""
        for observation in entity.observations:
            if self.include_empty and not observation.classification:
                return True
            if any(self._matches_type(classification.type) for classification in observation.classification):
                return True
        return False

    def build_option(
        self,
        *,
        id: str,
        label: str,
        default_enabled: bool = True,
        sort_key: int | None = None,
    ) -> FilterOption:
        """Build an option declaring this same policy for both Scene and history filtering."""
        return FilterOption(
            id=id,
            label=label,
            predicate=self,
            classification_filter=self,
            default_enabled=default_enabled,
            sort_key=sort_key,
        )


def make_classification_predicate(
    types: Sequence[str],
    *,
    include_empty: bool = False,
) -> ClassificationFilter:
    """Create a callable classification policy, optionally including unclassified objects."""
    return ClassificationFilter(frozenset(t for t in types if t), include_empty=include_empty)


def make_other_classification_predicate(excluded: Iterable[str]) -> ClassificationFilter:
    """Create a callable policy matching classes outside the supplied exclusion set."""
    return ClassificationFilter(frozenset(item for item in excluded if item), exclude=True)


@dataclass(slots=True, frozen=True)
class ClassFilterSpec:
    """Declarative description of a class-based filter option."""

    option_id: str
    label: str
    types: tuple[str, ...]
    include_empty: bool = False

    def build_option(self, sort_key: int) -> FilterOption:
        """Create a FilterOption for this spec."""
        return make_classification_predicate(self.types, include_empty=self.include_empty).build_option(
            id=self.option_id, label=self.label, sort_key=sort_key
        )


__all__ = [
    "ClassificationFilter",
    "ClassFilterSpec",
    "make_classification_predicate",
    "make_other_classification_predicate",
]
