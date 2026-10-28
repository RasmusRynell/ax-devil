"""Session-owned Scene and history filtering, shared by display, tools and export."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.scene.filtering import process_scene
from ax_devil.modules.scene.model import Scene

from .filter_config import FilterConfig, FilterOption, FilterState
from .history_filtering import history_type_filter_keeps
from .measure_range import Measure, MeasureRange, entity_in_ranges
from .predicate_utils import ClassificationFilter

if TYPE_CHECKING:
    from ax_devil.modules.data_sources.scene_history import ObjectHistory


class SessionFilter(QObject):
    """Own filter state and notify every consumer once after each effective edit.

    Besides the class toggles, a range per ``Measure`` narrows the Scene by what the decoder reported. History filtering
    ignores ranges: a recorded object has no single score or confidence.
    """

    changed = Signal()

    def __init__(self, config: FilterConfig | None = None) -> None:
        super().__init__()
        self._config = config or FilterConfig(
            options=(
                ClassificationFilter(frozenset(), include_empty=True, exclude=True).build_option(
                    id="all", label="All Entities"
                ),
            ),
            name="Simple Default Filter",
        )
        self._state = FilterState(self._config)
        self._ranges = {measure: MeasureRange() for measure in Measure}

    @property
    def options(self) -> tuple[FilterOption, ...]:
        """Return the configured controls in display order."""
        return self._config.sorted_options()

    @property
    def supports_history_filtering(self) -> bool:
        """Return whether the decoder supplies classification policies for history."""
        return self._config.supports_history_filtering

    @property
    def cache_identity(self) -> Hashable:
        """Return an opaque identity for the current filtering behavior."""
        return (id(self._config), tuple(self._state.items()), tuple(self._ranges.values()))

    @property
    def changed_count(self) -> int:
        """Return how many controls differ from the decoder's defaults, counting each measure with a range set."""
        defaults = {option.id: option.default_enabled for option in self._config.options}
        moved = sum(enabled != defaults[option_id] for option_id, enabled in self._state.items())
        return moved + sum(bounds != MeasureRange() for bounds in self._ranges.values())

    def is_enabled(self, option_id: str) -> bool:
        """Return whether a control is enabled."""
        return self._state.is_enabled(option_id)

    def set_enabled(self, option_id: str, enabled: bool) -> None:
        """Set one control and notify after an effective change."""
        before = self.cache_identity
        self._state.set_enabled(option_id, enabled)
        if before != self.cache_identity:
            self.changed.emit()

    def range(self, measure: Measure) -> MeasureRange:
        """Return the range kept for *measure*."""
        return self._ranges[measure]

    def set_range(self, measure: Measure, measure_range: MeasureRange) -> None:
        """Set the range kept for *measure* and notify after an effective change."""
        before = self.cache_identity
        self._ranges[measure] = measure_range
        if before != self.cache_identity:
            self.changed.emit()

    def set_all_enabled(self, enabled: bool) -> None:
        """Enable or disable every control and notify once after an effective change."""
        before = self.cache_identity
        for option in self._config.options:
            self._state.set_enabled(option.id, enabled)
        if before != self.cache_identity:
            self.changed.emit()

    def reset(self) -> None:
        """Restore the decoder's default toggles and clear every range, notifying once after an effective change."""
        before = self.cache_identity
        self._state = FilterState(self._config)
        self._ranges = {measure: MeasureRange() for measure in Measure}
        if before != self.cache_identity:
            self.changed.emit()

    def process_scene(self, scene: Scene) -> Scene:
        """Project a Scene using the decoder's predicates, then the ranges."""
        scene = process_scene(scene, self._config, self._state)
        active = {measure: bounds for measure, bounds in self._ranges.items() if bounds != MeasureRange()}
        if not active:
            return scene
        kept = {key: entity for key, entity in scene.entities.items() if entity_in_ranges(entity, active)}
        return scene if len(kept) == len(scene.entities) else replace(scene, entities=kept)

    def filter_history(self, objects: Sequence[ObjectHistory]) -> list[ObjectHistory]:
        """Keep recorded objects using the decoder's classification policy."""
        kept_by_types: dict[tuple[str, ...], bool] = {}
        kept: list[ObjectHistory] = []
        for history in objects:
            keep = kept_by_types.get(history.types)
            if keep is None:
                keep = kept_by_types[history.types] = history_type_filter_keeps(
                    history.types, self._config, self._state
                )
            if keep:
                kept.append(history)
        return kept

    def snapshot(self) -> SessionFilter:
        """Return a detached filter unaffected by subsequent session edits."""
        snapshot = SessionFilter(self._config)
        snapshot._state = FilterState(self._config, initial=dict(self._state.items()))
        snapshot._ranges = dict(self._ranges)
        return snapshot
