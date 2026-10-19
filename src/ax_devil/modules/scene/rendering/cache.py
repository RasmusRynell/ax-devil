"""Cache-backed scene overlay for prepared drawings and hover."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Protocol

from ax_devil.modules.scene.inspection import EntitySummary, entity_summary
from ax_devil.modules.scene.model import Entity, Scene
from ax_devil.modules.scene.rendering.catalog import (
    SceneRenderCatalog,
    get_built_in_scene_render_catalog,
)
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.engine.data_types import HoverHit
from ax_devil.modules.video_player.engine.quick.preparation import (
    EMPTY_DRAWING,
    DrawingBuffer,
    DrawingSettings,
    PreparedDrawing,
)
from ax_devil.modules.video_player.engine.render_context import RenderContext

logger = get_logger(__name__)

FilteredSceneKey = Hashable | None
RenderCacheKey = tuple[FilteredSceneKey, tuple[str, str]]
SceneRenderCatalogProvider = Callable[[], SceneRenderCatalog | None]
CatalogErrorIdentity = tuple[str, str, str]


class ReportedCatalogErrors:
    """Remember the most recently seen catalog errors so each is logged once while it stays recent."""

    def __init__(self, limit: int = 256) -> None:
        self._limit = limit
        self._recent: dict[CatalogErrorIdentity, None] = {}

    def is_new(self, identity: CatalogErrorIdentity) -> bool:
        """Record ``identity`` as most recent and return whether it was not already remembered."""
        is_new = identity not in self._recent
        self._recent.pop(identity, None)
        self._recent[identity] = None
        if len(self._recent) > self._limit:
            del self._recent[next(iter(self._recent))]
        return is_new


class SceneFilter(Protocol):
    """Filter adapter used by cached scene overlays."""

    @property
    def cache_identity(self) -> Hashable:
        """Return an opaque identity for the current filtering behavior."""

    def process_scene(self, scene: Scene) -> Scene:
        """Return a filtered Scene."""


@dataclass(frozen=True, slots=True)
class _IndexedHit:
    """Cached hit-test geometry for one entity."""

    target_id: str
    bounds: tuple[float, float, float, float]
    entity: Entity
    area: float


class CachedSceneOverlay:
    """Cache-backed overlay object for one displayed source scene.

    The catalog is *catalog_provider*'s, which answers None while it is not ready; the overlay then draws nothing and
    offers no hover targets, so the caller redraws once a catalog arrives. Without a provider the overlay uses
    *catalog*, or the built-in catalog compiled here, for callers that have no catalog selection.
    """

    def __init__(
        self,
        *,
        scene: Scene,
        scene_filter: SceneFilter | None = None,
        catalog: SceneRenderCatalog | None = None,
        catalog_provider: SceneRenderCatalogProvider | None = None,
        reported_errors: ReportedCatalogErrors | None = None,
    ) -> None:
        self._scene = scene
        self._scene_filter = scene_filter
        self._catalog_provider = catalog_provider
        self._catalog = catalog
        self._filtered_key: FilteredSceneKey | None = None
        self._filtered_scene: Scene | None = None
        self._hover_key: FilteredSceneKey | None = None
        self._hover_hits: list[_IndexedHit] | None = None
        self._hover_hits_by_id: dict[str, _IndexedHit] = {}
        self._render_key: RenderCacheKey | None = None
        self._settings: DrawingSettings | None = None
        self._drawing: PreparedDrawing | None = None
        self._reported_errors = reported_errors if reported_errors is not None else ReportedCatalogErrors()
        self._hover_hits_by_target: dict[str, HoverHit] = {}

    def prepare_drawing(self, context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
        """Return the prepared quads for this overlay and exact drawing settings."""
        catalog = self._current_catalog()
        if catalog is None:
            return EMPTY_DRAWING
        filtered_scene = self.filtered_scene()
        render_key = (self._filtered_scene_key(), catalog.rendering_identity)
        covered = self._settings is not None and self._settings.covers(buffer.settings)
        cache_hit = self._render_key == render_key and self._drawing is not None and covered
        if cache_hit:
            assert self._drawing is not None
            drawing = self._drawing
        else:
            diagnostics: list[CatalogDiagnostic] = []
            catalog.render_scene(filtered_scene, context, buffer, diagnostics=diagnostics)
            drawing = buffer.finish()
            for diagnostic in diagnostics:
                identity = (catalog.content_hash, diagnostic.code, diagnostic.location)
                if self._reported_errors.is_new(identity):
                    logger.warning(f"Render catalog {catalog.catalog_id}: {diagnostic.location}: {diagnostic.message}")
            self._render_key = render_key
            self._settings = buffer.settings
            self._drawing = drawing
        return drawing

    def hit_test(self, nx: float, ny: float) -> HoverHit | None:
        """Return smallest enclosing hover region for a normalized point."""
        if self._catalog_provider is not None and self._current_catalog() is None:
            return None
        best: _IndexedHit | None = None
        for indexed in self._indexed_hits():
            x0, y0, w, h = indexed.bounds
            x1 = x0 + w
            y1 = y0 + h
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            if best is None or indexed.area < best.area:
                best = indexed

        if best is None:
            return None
        return self._hover_hit_from_indexed(best)

    def get_hit_by_id(self, target_id: str) -> HoverHit | None:
        """Return hover payload for a known target id, if it still exists."""
        if self._catalog_provider is not None and self._current_catalog() is None:
            return None
        self._indexed_hits()
        indexed = self._hover_hits_by_id.get(target_id)
        if indexed is None:
            return None
        return self._hover_hit_from_indexed(indexed)

    def filtered_scene(self) -> Scene:
        """Return the cached Scene projection for the current filter state."""
        key = self._filtered_scene_key()
        if self._filtered_key == key and self._filtered_scene is not None:
            return self._filtered_scene

        filtered_scene = self._scene
        if self._scene_filter is not None:
            try:
                filtered_scene = self._scene_filter.process_scene(self._scene)
            except RuntimeError:
                logger.debug("Scene filtering failed during drawing preparation", exc_info=True)

        self._filtered_key = key
        self._filtered_scene = filtered_scene
        return filtered_scene

    def _indexed_hits(self) -> list[_IndexedHit]:
        key = self._filtered_scene_key()
        if self._hover_key == key and self._hover_hits is not None:
            return self._hover_hits

        filtered_scene = self.filtered_scene()
        hits: list[_IndexedHit] = []
        for entity in filtered_scene.entities.values():
            observation = entity.latest_observation
            if observation is None:
                continue
            bounds = observation.geometry.as_xywh()
            hits.append(
                _IndexedHit(
                    target_id=str(entity.id),
                    bounds=bounds,
                    entity=entity,
                    area=bounds[2] * bounds[3],
                )
            )

        self._hover_key = key
        self._hover_hits_by_target.clear()
        self._hover_hits = hits
        self._hover_hits_by_id = {indexed.target_id: indexed for indexed in hits}
        return hits

    def _filtered_scene_key(self) -> FilteredSceneKey:
        if self._scene_filter is None:
            return None

        return self._scene_filter.cache_identity

    def _current_catalog(self) -> SceneRenderCatalog | None:
        if self._catalog_provider is not None:
            return self._catalog_provider()
        if self._catalog is None:
            self._catalog = get_built_in_scene_render_catalog()
        return self._catalog

    def _hover_hit_from_indexed(self, indexed: _IndexedHit) -> HoverHit:
        hit = self._hover_hits_by_target.get(indexed.target_id)
        if hit is None:
            summary = entity_summary(indexed.entity)
            hit = HoverHit(
                target_id=indexed.target_id,
                bounds=indexed.bounds,
                label=f"{summary.entity_id} {summary.class_name}",
                detail=_hover_detail(summary),
                color_key=summary.class_name,
            )
            self._hover_hits_by_target[indexed.target_id] = hit
        return hit


def _hover_detail(summary: EntitySummary) -> str:
    """Return the facts the decoder reported for the entity in one line, leaving out those it did not."""
    parts: list[str] = []
    if summary.conf is not None:
        parts.append(f"conf {summary.conf:.2f}")
    if summary.speed is not None:
        parts.append(f"speed {summary.speed:.1f}")
    return " · ".join(parts)
