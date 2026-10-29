"""Scene inspection update delivery for video viewer side panels."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.scene.model import Scene

SceneRefilter = Callable[[], Scene | None]
"""Re-applies the current filters to the shown frame's Scene."""


class SceneInspectorSink(Protocol):
    """Receives Scene inspection updates from frame presentation."""

    def clear(self) -> None:
        """Clear current contents."""

    def set_playing(self, playing: bool) -> None:
        """Coalesce visual updates while playing, and flush the displayed snapshot when paused."""

    def update_scene(
        self,
        scene: Scene | None,
        frame_id: FrameIdentifier | None,
        metadata: dict[str, Any] | None,
        refilter: SceneRefilter | None = None,
        source_count: int | None = None,
    ) -> None:
        """Publish the Scene shown for a displayed frame; *source_count* is its object count before filtering."""


@dataclass(frozen=True, slots=True)
class SceneInspectionUpdate:
    """Data needed to update a Scene inspector after a frame is displayed."""

    scene: Scene | None
    frame_id: FrameIdentifier | None
    metadata: dict[str, Any]
    refilter: SceneRefilter | None = None
    source_count: int | None = None


def deliver_scene_inspection_update(sink: SceneInspectorSink | None, update: SceneInspectionUpdate) -> None:
    """Deliver a Scene inspection update immediately."""
    if sink is None:
        return
    sink.update_scene(update.scene, update.frame_id, update.metadata, update.refilter, source_count=update.source_count)
