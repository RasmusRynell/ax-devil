# scene.rendering module

Purpose: Scene-to-drawing catalog execution. This package turns a filtered `Scene` into batched drawing calls on the
generic `DrawingTarget` owned by `ax_devil.modules.video_player`, and keeps the Scene Render Catalogs that decide how
each entity and relation is drawn. The boundary with the video player is in
[Draw System Architecture](../../../../../docs/architecture/draw-system.md); the catalog language itself is
documented by `ax-devil catalog reference`, generated from the runtime definitions.

## Quick map

- `cache.py`: `CachedSceneOverlay`, the per-Scene adapter that caches the filtered Scene and prepared drawing, indexes
  hover targets and reports preparation metrics; `ReportedCatalogErrors` limits repeated catalog error logs.
- `catalog.py`: `SceneRenderCatalog` recipe routing and per-recipe row-column execution, and
  `SceneRenderCatalogLoader` validation and compilation.
- `catalog_store.py`: `SceneRenderCatalogStore`, the user-store file lifecycle, the listing with the packaged built-in
  catalogs first, and the default catalog choice.
- `catalog_manager.py`: `SceneRenderCatalogManager` (discovery, compiled reuse, file watching, default catalog, apply
  to all) and `SceneRenderCatalogSelection` (one consumer's active catalog and visibility choices).
- `visibility.py`: the typed `OverlayFeature` groups a view can hide, and immutable per-view visibility.
- `catalog_definitions/`: the packaged built-in catalogs (schema version 3) and their generated JSON Schema.
- `template_runtime/`: the catalog language. `definitions.py` is the closed set of value types, operations and
  primitives with their limits as data; `schema.py` derives the JSON Schema from it; `compiler.py` checks scope,
  types, references and expansion limits; `program.py` and `executable.py` lower validated plans to one Python
  function per recipe that evaluates every routed row at once over NumPy columns; `kernels.py` is that runtime.

## How it runs

Routing is latest-observation based: an entity with no observations is skipped; its primary classification selects a
classification recipe, else the `classified` fallback, else `unclassified`. A relation recipe is selected by relation
type and skipped unless both endpoints have observations in the filtered Scene, so entity filtering applies to
relations without a separate policy. Each recipe then evaluates once per frame for all its rows; only demanded Scene
fields are read, once per column.

`CachedSceneOverlay` keys its filtered Scene on the filter's cache identity and its prepared drawing on that key, the
target dimensions and settings, and the catalog's rendering identity. The rendering identity ignores descriptive
metadata and mapping order but includes the compiler semantic revision and hidden features, so a visibility change
invalidates prepared drawing without refiltering or rebuilding the hover index. Hover targets come from the same
filtered Scene; hit testing picks the smallest box containing the cursor.

## Rules

- Keep Scene semantics here; the video player receives only prepared drawings, hover hits and metrics.
- Prefer adding or replacing draw recipes in catalog JSON over adding render-wrapper layers. The built-in catalog
  keeps presentation choices such as motion symbols in JSON (`lookup_text`), not in Python.
- Validation and compilation never run on the frame-drawing path, and selectors and lane widgets never read,
  validate or compile catalog files during construction. Listing is metadata discovery only; full validation happens
  on select, reload, create, apply to all and use as default.
- Visibility is applied by compiling a variant from the already validated source, never per frame. The whole document
  is validated first, so hidden components still have to be valid.
- `pick_color` hashes with CRC-32, not `hash()`, so an id keeps its color across runs.
- Catalog execution stays compiled Python. A C++ extension measured about three times faster, but is not worth
  maintaining the semantics twice and shipping binary wheels; revisit only with new measurements.

## Catalog files and selection

The **built-in catalogs** are the packaged `catalog_definitions/*.json` files, read in place and never written.
The **default catalog** is the one new selections start from: Standard until the user picks another with
**Use as default**, stored in `default_catalog.txt` beside the user catalogs (`built-in:<file name>` for another
built-in). Deleting the default clears the choice.

Catalog choices have three scopes:

| Scope | Call | Effect |
|---|---|---|
| One view | `SceneRenderCatalogSelection.select_catalog(path)` | That viewer or lane switches. |
| All open views | `SceneRenderCatalogManager.apply_to_all(path)` | Compiled first; on failure nothing changes and the error is raised. |
| New views | `SceneRenderCatalogManager.set_default_catalog(path)` | Compiled first; on failure the previous default stays. Open views keep their catalog. |

A selection's active path is what the user chose; its active catalog is the last one that compiled, which after a
failure may be an older revision, another file, or the built-in default with the reason in its status. Every load
attempt of the active path records a failure, and only a successful compile clears it, so `active_catalog_loaded()`
is true exactly when the latest attempt compiled and the selector can enable **Apply to all** and **Use as default**.
The manager watches every listed file and its directory and, after 150 ms of quiet, reloads each selection whose
active path changed; other selections are untouched. Live viewers share one selection between controller and media
tools; offline lanes each own one.

## Testing focus

- Catalog loading, validation and schema: `tests/modules/scene/rendering/test_catalog_loader.py`,
  `test_template_schema.py`.
- Compiler and language semantics: `tests/modules/scene/rendering/test_template_runtime_compiler.py`.
- Recipe output for the built-in catalog: `tests/modules/scene/rendering/test_scene_rendering_runtime.py`.
- Overlay caching, hover and metrics: `tests/modules/scene/rendering/test_cache.py`.
- Visibility specialization and retained inputs: `tests/modules/scene/rendering/test_overlay_visibility.py`.
- Catalog manager and selection: `tests/modules/scene/rendering/test_catalog_manager.py`.
