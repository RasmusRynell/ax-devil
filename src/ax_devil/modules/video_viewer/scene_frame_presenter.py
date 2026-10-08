"""Presentation assembly for video frames with optional Scene overlays."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any
from weakref import ref

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.scene.rendering.cache import CachedSceneOverlay, ReportedCatalogErrors, SceneFilter
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player import VideoFrame, VideoOverlayData
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays

from .overlay_persistence import OverlayPersistencePolicy
from .scene_inspection import SceneInspectionUpdate

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalog

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SceneFramePresentation:
    """Result of assembling one displayable frame."""

    display_frame: VideoFrameWithOverlays
    selected_overlay: OverlayData | None
    overlay_reused: bool
    metadata: dict[str, Any]
    inspection: SceneInspectionUpdate


class SceneFramePresenter:
    """Assemble frame, Scene overlay, filtering, metadata, and inspector presentation."""

    def __init__(
        self,
        *,
        scene_filter: SceneFilter | None = None,
        scene_render_catalog: SceneRenderCatalog | None = None,
    ) -> None:
        self._scene_filter = scene_filter
        self._scene_render_catalog = scene_render_catalog
        self._reported_catalog_errors = ReportedCatalogErrors()
        self._cached_source: OverlayData | None = None
        self._cached_overlay: CachedSceneOverlay | None = None
        owner = ref(self)

        def current_catalog() -> SceneRenderCatalog | None:
            presenter = owner()
            return presenter.scene_render_catalog if presenter is not None else None

        self._catalog_provider = current_catalog

    def attach_scene_filter(self, scene_filter: SceneFilter | None) -> None:
        """Set the session filter used for future frame presentation."""
        self._scene_filter = scene_filter
        self.clear()

    def clear(self) -> None:
        """Release the single retained source sample and its prepared projection."""
        self._cached_source = None
        self._cached_overlay = None

    def attach_scene_render_catalog(self, scene_render_catalog: SceneRenderCatalog) -> None:
        """Set the Scene Render Catalog used for future overlay presentation."""
        self._scene_render_catalog = scene_render_catalog

    @property
    def scene_render_catalog(self) -> SceneRenderCatalog | None:
        """Return the active Scene Render Catalog override."""
        return self._scene_render_catalog

    def prepare_frame(
        self,
        frame_data: FrameData,
        candidate_overlay: OverlayData | None,
        *,
        overlay_policy: OverlayPersistencePolicy | None = None,
        overlay_metadata: dict[str, Any] | None = None,
    ) -> SceneFramePresentation:
        """Prepare a frame and its selected overlay for display."""
        selected_overlay = candidate_overlay
        metadata = self._merge_overlay_metadata(selected_overlay, overlay_metadata)
        overlay_reused = bool(metadata.get("overlay_reused", False))

        if overlay_policy is not None:
            selection = overlay_policy.select_overlay(frame_data.frame_id, candidate_overlay)
            selected_overlay = selection.overlay
            overlay_reused = selection.reused
            metadata = self._merge_overlay_metadata(selected_overlay, selection.to_metadata(), overlay_metadata)

        cached_overlay = self._build_cached_overlay(selected_overlay)
        filtered_scene = self._filtered_scene(selected_overlay, cached_overlay)

        return SceneFramePresentation(
            display_frame=self._to_display_frame(frame_data, selected_overlay, cached_overlay, metadata),
            selected_overlay=selected_overlay,
            overlay_reused=overlay_reused,
            metadata=metadata,
            inspection=SceneInspectionUpdate(
                scene=filtered_scene,
                frame_id=frame_data.frame_id,
                metadata=metadata,
                refilter=partial(self._filtered_scene, selected_overlay, cached_overlay),
            ),
        )

    def _to_display_frame(
        self,
        frame_data: FrameData,
        overlay_data: OverlayData | None,
        cached_overlay: CachedSceneOverlay | None,
        metadata: dict[str, Any],
    ) -> VideoFrameWithOverlays:
        video_frame = VideoFrame(
            image=frame_data.content,
            timestamp=frame_data.frame_id.timestamp_monotime_us / 1_000_000.0,
            timestamp_monotime_us=frame_data.frame_id.timestamp_monotime_us,
            frame_id=frame_data.frame_id.sequence_id,
            metadata=frame_data.metadata,
        )

        video_overlay = None
        if overlay_data is not None:
            assert cached_overlay is not None, "Scene overlays require a cached projection"
            video_overlay = VideoOverlayData(
                drawing_generator=cached_overlay.prepare_drawing,
                timestamp=overlay_data.frame_id.timestamp_monotime_us / 1_000_000.0,
                timestamp_monotime_us=overlay_data.frame_id.timestamp_monotime_us,
                overlay_id=overlay_data.frame_id.sequence_id,
                metadata=metadata,
                interaction_provider=cached_overlay,
                metrics_provider=cached_overlay,
            )

        return VideoFrameWithOverlays(
            frame=video_frame,
            overlays=video_overlay,
        )

    def _build_cached_overlay(self, overlay: OverlayData | None) -> CachedSceneOverlay | None:
        if overlay is None:
            self.clear()
            return None
        previous = self._cached_source
        if (
            previous is not None
            and previous.content is overlay.content
            and previous.frame_id == overlay.frame_id
            and previous.source_id == overlay.source_id
        ):
            return self._cached_overlay
        self._cached_source = overlay
        self._cached_overlay = CachedSceneOverlay(
            scene=overlay.content,
            scene_filter=self._scene_filter,
            catalog=self._scene_render_catalog,
            catalog_provider=self._catalog_provider,
            reported_errors=self._reported_catalog_errors,
        )
        return self._cached_overlay

    @staticmethod
    def _filtered_scene(
        overlay: OverlayData | None,
        cached_overlay: CachedSceneOverlay | None,
    ) -> Scene | None:
        if overlay is None:
            return None
        scene = overlay.content
        assert cached_overlay is not None, "Scene overlays require a cached projection"
        try:
            return cached_overlay.filtered_scene()
        except Exception:
            logger.debug("Scene filtering failed for inspector", exc_info=True)
            return scene

    @staticmethod
    def _merge_overlay_metadata(
        overlay: OverlayData | None,
        *metadata_layers: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if overlay is not None and overlay.metadata:
            metadata.update(overlay.metadata)
        for layer in metadata_layers:
            if layer:
                metadata.update(layer)
        return metadata


def describe_frame_id(frame_id: FrameIdentifier | None) -> str:
    """Return a compact representation of a frame identifier for debug metrics."""
    if frame_id is None:
        return "None"
    timestamp_s = frame_id.timestamp_monotime_us / 1_000_000.0
    return f"#{frame_id.sequence_id} @ {timestamp_s:.3f}s"


def describe_overlay_data(overlay: OverlayData | None, *, reused: bool = False) -> str:
    """Return a descriptive label for overlay debug metrics."""
    base = describe_frame_id(overlay.frame_id) if overlay is not None else "None"
    if reused:
        return f"{base} (reused)"
    return base
