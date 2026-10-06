"""Frozen scene filter — an immutable snapshot of filter state for export."""

from __future__ import annotations

from ax_devil.modules.filtering import FilterConfig, FilterState
from ax_devil.modules.scene.filtering import process_scene
from ax_devil.modules.scene.model import Scene


class FrozenSceneFilter:
    """Immutable snapshot of a SceneFilter's state at a point in time.

    Satisfies the SceneFilter protocol but cannot be mutated.
    Used by the export pipeline to guarantee filters don't change mid-export.
    """

    def __init__(self, config: FilterConfig, state: FilterState) -> None:
        self._config = config
        # Snapshot the enabled flags and id query into a new FilterState
        self._state = FilterState(config, initial=dict(state.items()), id_query=state.id_query)

    @property
    def filter_config(self) -> FilterConfig:
        """Return the filter configuration."""
        return self._config

    @property
    def filter_state(self) -> FilterState:
        """Return the snapshotted filter state."""
        return self._state

    def process_scene(self, scene: Scene) -> Scene:
        """Filter scene using the snapshotted state."""
        return process_scene(scene, self._config, self._state)
