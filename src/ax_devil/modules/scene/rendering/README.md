# scene.rendering module

Purpose: Scene-to-drawing catalog execution. This package turns a filtered `Scene` into batched drawing calls on the
generic `DrawingTarget` owned by `ax_devil.modules.video_player`, and keeps the Scene Render Catalogs that decide how
each entity and relation is drawn.

How the draw system works end to end, the built-in catalog's recipes, the catalog language and the open work are
documented in [Draw System Architecture](../../../../../docs/architecture/draw-system.md). Rules are in
[Domain Invariants](../../../../../docs/domain/invariants.md#rendering).

## Quick map

- `cache.py`: `CachedSceneOverlay`, the per-Scene adapter that caches the filtered Scene and prepared drawing, indexes
  hover targets and reports preparation metrics; `ReportedCatalogErrors` limits repeated catalog error logs.
- `catalog.py`: `SceneRenderCatalog` recipe routing and per-recipe row-column execution and
  `SceneRenderCatalogLoader` validation and compilation.
- `catalog_store.py`: `SceneRenderCatalogStore`, the user-store file lifecycle, the listing with the six packaged built-in
  catalogs first, and the default catalog choice.
- `catalog_manager.py`: `SceneRenderCatalogManager` (discovery, compiled reuse, default catalog, apply to all) and
  `SceneRenderCatalogSelection` (one consumer's active catalog and visibility choices).
- `visibility.py`: typed semantic feature groups and immutable per-view visibility preferences.
- `catalog_definitions/`: the six packaged built-in catalogs (schema version 3) and their generated JSON Schema.
- `template_runtime/`: the catalog language: definitions, generated schema, compiler and row-column execution.
  Per-file ownership is listed under
  [Catalog JSON And Compilation](../../../../../docs/architecture/draw-system.md#catalog-json-and-compilation).

## Design rules

- Keep Scene semantics here; the video player receives only prepared drawings, hover hits and metrics.
- Prefer adding or replacing draw recipes in catalog JSON over adding render-wrapper layers.
- Keep catalog validation and compilation off the frame-drawing path.

## Testing focus

- Catalog loading, validation and schema: `tests/modules/scene/rendering/test_catalog_loader.py`,
  `test_template_schema.py`.
- Compiler and language semantics: `tests/modules/scene/rendering/test_template_runtime_compiler.py`.
- Recipe output for the built-in catalog: `tests/modules/scene/rendering/test_scene_rendering_runtime.py`.
- Overlay caching, hover and metrics: `tests/modules/scene/rendering/test_cache.py`.
- Visibility specialization and retained inputs: `tests/modules/scene/rendering/test_overlay_visibility.py`.
- Catalog manager and selection: `tests/modules/scene/rendering/test_catalog_manager.py`.
