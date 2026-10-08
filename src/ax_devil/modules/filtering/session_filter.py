"""Session-owned Scene and history filtering, shared by display, tools and export."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.scene.filtering import matches_id_query, process_scene
from ax_devil.modules.scene.model import Scene

from .filter_config import FilterConfig, FilterOption, FilterState
from .history_filtering import history_type_filter_keeps
from .predicate_utils import ClassificationFilter

if TYPE_CHECKING:
    from ax_devil.modules.data_sources.scene_history import ObjectHistory


class SessionFilter(QObject):
    """Own filter state and notify every consumer once after each effective edit."""

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
        return (id(self._config), tuple(self._state.items()), self._state.id_query)

    @property
    def enabled_count(self) -> int:
        """Return the number of enabled controls."""
        return sum(enabled for _, enabled in self._state.items())

    @property
    def id_query(self) -> str:
        """Return the current entity-id search."""
        return self._state.id_query

    def is_enabled(self, option_id: str) -> bool:
        """Return whether a control is enabled."""
        return self._state.is_enabled(option_id)

    def set_enabled(self, option_id: str, enabled: bool) -> None:
        """Set one control and notify after an effective change."""
        before = self.cache_identity
        self._state.set_enabled(option_id, enabled)
        if before != self.cache_identity:
            self.changed.emit()

    def toggle_all(self) -> None:
        """Disable all enabled controls, or enable all if any are disabled."""
        enabled = self.enabled_count != len(self._config.options)
        for option in self._config.options:
            self._state.set_enabled(option.id, enabled)
        self.changed.emit()

    def set_id_query(self, query: str) -> None:
        """Search entity ids using trimmed text and notify after an effective change."""
        query = query.strip()
        if query != self._state.id_query:
            self._state.id_query = query
            self.changed.emit()

    def process_scene(self, scene: Scene) -> Scene:
        """Project a Scene using the decoder's predicates and current search."""
        return process_scene(scene, self._config, self._state)

    def filter_history(self, objects: Sequence[ObjectHistory]) -> list[ObjectHistory]:
        """Keep recorded objects using id search and the decoder's classification policy."""
        kept_by_types: dict[tuple[str, ...], bool] = {}
        kept: list[ObjectHistory] = []
        for history in objects:
            if not matches_id_query(history.entity_id, self._state):
                continue
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
        snapshot._state = FilterState(self._config, initial=dict(self._state.items()), id_query=self._state.id_query)
        return snapshot
