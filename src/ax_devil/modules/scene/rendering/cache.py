"""Cache-backed scene overlay for prepared drawings, hover, and metrics."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from ax_devil.modules.diagnostics.metrics_gate import is_metrics_enabled
from ax_devil.modules.scene.inspection import build_entity_hover_html
from ax_devil.modules.scene.model import Entity, Scene
from ax_devil.modules.scene.rendering.catalog import (
    SceneRenderCatalog,
    get_built_in_scene_render_catalog,
)
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_player.engine.data_types import (
    DrawingBuildReason,
    DrawingPreparationMetrics,
    HoverHit,
)
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext

logger = get_logger(__name__)

FilteredSceneKey = Hashable | None
RenderCacheKey = tuple[
    FilteredSceneKey,
    tuple[int, int, DrawingSettings],
    tuple[str, str],
]
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
    """Cache-backed overlay object for one displayed source scene."""

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
        if catalog is not None or catalog_provider is not None:
            self._catalog = catalog
        else:
            self._catalog = get_built_in_scene_render_catalog()
        self._filtered_key: FilteredSceneKey | None = None
        self._filtered_scene: Scene | None = None
        self._hover_key: FilteredSceneKey | None = None
        self._hover_hits: list[_IndexedHit] | None = None
        self._hover_hits_by_id: dict[str, _IndexedHit] = {}
        self._render_key: RenderCacheKey | None = None
        self._drawing: PreparedDrawing | None = None
        self.latest_metrics = DrawingPreparationMetrics()
        self._pending_filter_time_ms: float | None = None
        self.diagnostics: tuple[CatalogDiagnostic, ...] = ()
        self._reported_errors = reported_errors if reported_errors is not None else ReportedCatalogErrors()
        self._hover_cards: dict[str, str] = {}

    def prepare_drawing(self, context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
        """Return final backend data for this overlay and exact drawing settings."""
        filtered_scene = self.filtered_scene()
        catalog = self._current_catalog()
        render_key = self._render_cache_key(context, buffer.settings, catalog)
        cache_hit = self._render_key == render_key and self._drawing is not None
        capture = is_metrics_enabled()
        generation_time_ms = None
        reasons: tuple[DrawingBuildReason, ...] = ()
        if cache_hit:
            assert self._drawing is not None
            drawing = self._drawing
        else:
            if capture and self._render_key is None:
                reasons = (DrawingBuildReason.INITIAL,)
            elif capture and self._render_key is not None:
                reasons = tuple(
                    reason
                    for old, new, reason in zip(
                        self._render_key,
                        render_key,
                        (
                            DrawingBuildReason.FILTER,
                            DrawingBuildReason.TARGET,
                            DrawingBuildReason.CATALOG,
                        ),
                    )
                    if old != new
                )
            start = perf_counter() if capture else 0.0
            diagnostics: list[CatalogDiagnostic] = []
            catalog.render_scene(filtered_scene, context, buffer, diagnostics=diagnostics)
            drawing = buffer.finish()
            self.diagnostics = tuple(diagnostics)
            for diagnostic in diagnostics:
                identity = (catalog.content_hash, diagnostic.code, diagnostic.location)
                if self._reported_errors.is_new(identity):
                    logger.warning(f"Render catalog {catalog.catalog_id}: {diagnostic.location}: {diagnostic.message}")
            if capture:
                generation_time_ms = (perf_counter() - start) * 1000
            self._render_key = render_key
            self._drawing = drawing
        if capture:
            self.latest_metrics = DrawingPreparationMetrics(
                filter_time_ms=self._pending_filter_time_ms,
                generation_time_ms=generation_time_ms,
                filter_cache_hit=self._pending_filter_time_ms is None,
                drawing_cache_hit=cache_hit,
                build_reasons=reasons,
                input_entity_count=len(self._scene.entities),
                filtered_entity_count=len(filtered_scene.entities),
                primitive_counts=drawing.counts,
            )
        self._pending_filter_time_ms = None
        return drawing

    def hit_test(self, nx: float, ny: float) -> HoverHit | None:
        """Return smallest enclosing hover region for a normalized point."""
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
        self._indexed_hits()
        indexed = self._hover_hits_by_id.get(target_id)
        if indexed is None:
            return None
        return self._hover_hit_from_indexed(indexed)

    def latest_drawing_preparation_metrics(self) -> DrawingPreparationMetrics:
        """Return metrics from the most recent drawing preparation."""
        return self.latest_metrics

    def filtered_scene(self) -> Scene:
        """Return the cached Scene projection for the current filter state."""
        key = self._filtered_scene_key()
        if self._filtered_key == key and self._filtered_scene is not None:
            return self._filtered_scene

        capture = is_metrics_enabled()
        start = perf_counter() if capture else 0.0
        filtered_scene = self._scene
        if self._scene_filter is not None:
            try:
                filtered_scene = self._scene_filter.process_scene(self._scene)
            except RuntimeError:
                logger.debug("Scene filtering failed during drawing preparation", exc_info=True)

        self._pending_filter_time_ms = (perf_counter() - start) * 1000 if capture else None
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
        self._hover_cards.clear()
        self._hover_hits = hits
        self._hover_hits_by_id = {indexed.target_id: indexed for indexed in hits}
        return hits

    def _filtered_scene_key(self) -> FilteredSceneKey:
        if self._scene_filter is None:
            return None

        return self._scene_filter.cache_identity

    def _current_catalog(self) -> SceneRenderCatalog:
        provided_catalog = self._catalog_provider() if self._catalog_provider is not None else None
        if provided_catalog is not None:
            return provided_catalog
        if self._catalog is None:
            self._catalog = get_built_in_scene_render_catalog()
        return self._catalog

    def _render_cache_key(
        self, context: RenderContext, settings: DrawingSettings, catalog: SceneRenderCatalog
    ) -> RenderCacheKey:
        return (
            self._filtered_scene_key(),
            (context.width, context.height, settings),
            catalog.rendering_identity,
        )

    def _hover_hit_from_indexed(self, indexed: _IndexedHit) -> HoverHit:
        card_html = self._hover_cards.get(indexed.target_id)
        if card_html is None:
            card_html = build_entity_hover_html(indexed.entity)
            self._hover_cards[indexed.target_id] = card_html
        return HoverHit(
            target_id=indexed.target_id,
            bounds=indexed.bounds,
            card_html=card_html,
        )
