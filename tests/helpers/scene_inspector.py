"""Scene inspection sink shared by live and offline presentation tests."""

from __future__ import annotations

from typing import Any

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.video_viewer.scene_inspection import SceneRefilter


class RecordingSceneInspector:
    """Record delivered scenes and clear notifications at the inspection boundary."""

    def __init__(self) -> None:
        self.cleared = False
        self.updates: list[tuple[Scene | None, FrameIdentifier | None, dict[str, Any] | None]] = []

    def clear(self) -> None:
        """Record the notification and discard the previous inspected scenes."""
        self.cleared = True
        self.updates.clear()

    def update_scene(
        self,
        scene: Scene | None,
        frame_id: FrameIdentifier | None,
        metadata: dict[str, Any] | None,
        refilter: SceneRefilter | None = None,
    ) -> None:
        """Record an inspector update."""
        self.updates.append((scene, frame_id, metadata))
