# Draw System Architecture

This document describes the Scene draw system: where behavior lives, which contracts matter, and which parts should stay separate.

## Core Ownership

The draw system has two intentionally separate halves.

`ax_devil.modules.scene.rendering` owns semantic rendering. It knows about `Scene`, `Entity`, `Observation`, `Classification`, filters, draw recipes, catalog JSON, template compilation, and hover target indexing.

`ax_devil.modules.video_player` owns generic drawing. It knows about video frames, prepared geometry, paths and glyphs, Qt Quick display and image export, viewport math, hover card placement, and metrics collection. It must not become Scene-aware.

The boundary object is `VideoOverlayData`. Scene code supplies a `drawing_generator`, `interaction_provider`, and `metrics_provider`; the video player calls those protocols without knowing what domain object produced them.

## Runtime Flow

```text
FrameData + OverlayData(Scene)
  -> SceneFramePresenter.prepare_frame(...)
  -> VideoFrameWithOverlays
  -> VideoOverlayData(
       drawing_generator=CachedSceneOverlay.prepare_drawing,
       interaction_provider=CachedSceneOverlay,
       metrics_provider=CachedSceneOverlay,
     )
  -> FrameDisplay.display_frame(...)
  -> VideoFrameRenderer GUI-thread preparation (coalesced to latest frame)
  -> VideoFrameWithOverlays.prepare_overlays(...)
  -> CachedSceneOverlay.prepare_drawing(RenderContext, DrawingBuffer)
  -> optional filter
  -> SceneRenderCatalog.render_scene(...)
  -> entities routed to recipes; one row table per CompiledSceneRenderRecipe
  -> relations routed to CompiledSceneRelationRenderRecipe row tables
  -> one generated row-column update function per recipe evaluates all its rows at once
  -> batches of primitives for rows that completed without failures
  -> DrawingBuffer queues batches per kind and prepares each kind once at finish()
  -> PreparedDrawing (vertex bytes, resolved paths, shaped glyph layouts)
  -> QuickOverlay binds retained Qt items (display and export)
```

Live and offline viewers differ only in how they obtain `FrameData` and `OverlayData`. Both pass frame and overlay data directly to `SceneFramePresenter` and converge on the same display model. Live synchronization packets are unwrapped by the stream controller; offline playback and export do not manufacture synchronization packets or arrival timestamps.

## Display Backends

`VideoFrameRenderer` retains the QWidget interaction shell and hosts a `QuickSurface`
(`QQuickWidget`) as the sole video and overlay renderer. Graphics Off selects
`QT_QUICK_BACKEND=software` at startup.
`QT_WIDGETS_RHI` controls widget presentation, not the viewer implementation.
Auto prepares accelerated presentation before the first window opens, so the first
viewer can be added without recreating it. Off uses software rendering. Explicit
environment overrides take precedence.
`QSG_RHI_BACKEND` and `QT_QUICK_BACKEND` remain Qt's backend overrides. Quick's software
backend supports the same paths and text when hardware acceleration is unavailable.

The `DrawingTarget` contract accepts batches of catalog-resolved boxes, circles, lines, points, polygons,
polylines, texts and labels: row i of every argument describes one primitive, and a `Paint` carries each style field
as one shared value or one value per row. Labels carry no `Paint`: each row is a hashable `LabelContent` (runs of
text with their colors and weights, or bars filled to a fraction, then size, family, background, padding, corner
radius and gap). `DrawingBuffer` queues batches per kind and prepares each kind once
for all its rows in `finish()`, so the fixed cost of the array operations is paid per frame rather than per
catalog step. Labels are prepared row by row as they arrive, since batching would save them nothing. On hardware
backends, solid rectangles, lines and convex polygons use retained QSGGeometryNode
triangle buffers with premultiplied vertex colors. Color-free rectangle topologies and whole-frame NumPy vertex
generation avoid per-frame Python vertex loops and SVG parsing; each vertex takes one of a few (pen or fill,
coverage) color classes, resolved from a small per-row palette. Geometry includes physical-pixel antialias
fringes, bevel joins and square caps; small edge-coverage differences from Qt Shape are expected. Lines are rotated
filled rectangles. Convex polygons get a fan fill with a coverage fringe and stroke rings with beveled outer
corners. Narrow rectangles, rounded rectangles (a nonzero `radius` in the box style), strokes below one physical pixel, dashed
styles, concave or degenerate polygons, circles, points, polylines and all software drawing use ShapePaths. Qt handles
curves, odd-even fills, arbitrary polygons and other
complex paths. All geometry of a frame is packed into
batches, split only at 60,000 vertices.
Geometry, paths, text and labels are separate pools and separate layers, drawn bottom to top in that order, so text
stays readable above shapes. Draw order across entities, and within a layer, is intentionally not preserved: overlapping
overlays are a filtering concern, and free order lets geometry batch into a few nodes instead of one per primitive.
Catalogs therefore never rely on one shape covering another; a fill and its outline belong in one primitive, and
anything drawn on a background belongs in a label.
Geometry items are selected by their ordinal within that kind, since their vertex payload changes with
movement anyway. Path and text items are matched by content (path description and paint; shaped glyph block
and color), independent of position. Adding, removing or reordering operations therefore moves retained
glyphs and triangulated paths instead of rebuilding them; equal content keeps its relative order. Each item pool retains
at most 32 unused items beyond the current workload, and clearing releases all items. There is no classification,
entity, or catalog-specific optimization in the backend. Shapes are not rendered
through QQuickPaintedItem or a CPU overlay image.

`PreparedDrawing` contains immutable per-kind layer tuples: screen-space vertex bytes, resolved Qt Shape properties, already shaped text with final positions/colors, and painted labels with their positions.

A label is laid out and painted once per distinct content, scale, DPI and DPR into a small premultiplied image
(`label_sprite`): its background is sized to its runs, so catalogs never need to measure text. Live sprites retain
their identity while displayed; a bounded cache keeps recently unused sprites. Measurement and painting use the same
DPI.
Layout includes glyph overhangs, and preparation places both image edges on device pixels so its text stays crisp.
Labels whose raster dimensions overflow Qt's integer dimensions or whose image allocation fails are omitted without
discarding neighboring labels. Quick submits all labels through one `LabelLayer`. Its render-thread node tree groups
images by sprite: the first image node owns the texture and the other instances borrow it. Moving labels update
rectangles; identical or returning content reuses its texture. Textures opt into Qt's atlas so small images can share
storage and rendering batches. Hidden groups retain up to 1,024 unused textures, limited additionally to 8 MiB of
source-image bytes (not a measurement of GPU allocation). Each hidden group retains at most four image nodes; active
groups keep at most four spare instances, with empty rectangles so removed labels leave no pixels. Visible groups
are never evicted. Cache eviction, clearing, reparenting and scene-graph invalidation release resources through the
native node tree on Qt's render thread; there is no process-global GPU cache or separate cleanup callback.
New content still needs a raster and upload; changing confidence values can reuse previously displayed score textures.
`PreparedDrawing` contains no Scene references, normalized primitive objects, evaluator state or Qt scene graph nodes.
Qt owns path tessellation, glyph atlas resources, buffer upload and rendering. Complex paths remain
resolved SVG input to Qt, not application-generated GPU geometry.

Catalog evaluation reports failures per row before anything reaches the `DrawingBuffer`: a row that fails any
demanded check emits none of its recipe's primitives, and other rows are unaffected. Successful output is published
only when scene evaluation completes. The surface owns a reusable buffer. It retains the preceding prepared drawing
to keep active text resources alive.

`QuickOverlay` compares final records in each pool and updates changed resources/transforms only.
Reused prepared drawings bypass item traversal. Paths use local coordinates and a separate origin;
movement preserves path geometry and shaped text. All preparation is on the GUI thread. Fonts,
text layouts, colors and geometry use bounded caches. Consumers treat their Qt value objects as
immutable and must not mutate a published drawing.

Numeric geometry caches bounded rectangle topologies. NumPy broadcasts shared topologies and packs their bytes into
batches; Qt-owned storage receives those bytes during scene graph synchronization. Materials and
geometry are owned by the node, and the attribute descriptor outlives all buffers. The software adaptation
uses the existing Shape renderer. Active text layouts remain available through weak references even when
the bounded inactive-layout LRU evicts their keys; movement therefore does not force reshaping.

Offscreen culling uses the surface viewport translated into overlay-local coordinates, rather
than the image bounds: overlays may remain visible in letterboxing. Pan and viewport-size changes
invalidate preparation even when the Scene and image dimensions are unchanged. Whole operations
outside conservative bounds are omitted before item allocation/update, on hardware and software.
Bounds include square-cap/bevel stroke extents and a two-physical-pixel antialias margin.
Paths may supply local unstroked bounds; custom paths without bounds remain drawable.
Text is shaped for its normal anchor first. When its layout rectangle is outside, a bounded lazy
cache of complete glyph-ink bounds checks overhangs before rejecting it. This avoids offscreen
glyph-node work but does not eliminate text shaping or catalog evaluation.

Text layout and anchors are shared with export. Quick uses QSGTextNode for native
glyph rendering. Video uses a QSGImageNode texture from the existing QImage contract;
this does not change decoding or remove RGB conversion. Repaints reuse the texture
when the image cache key is unchanged. On RHI backends, same-size images with the same
alpha semantics reuse the image node and Qt-owned texture storage; a QSGTexture adapter
enqueues the newest pixels in Qt's resource update batch. Size/alpha changes recreate the
node and owned texture together. Software rendering retains Qt's image texture path.
Texture resources remain per surface; they are not shared across independent RHI instances.
Comparison lanes reuse CPU upload-format conversion through a render-thread-local cache of
at most two QImages. This does not cache a decoded video history or eliminate pixel uploads. No framebuffer readback is
needed for display.

A single coalesced GUI-thread update prepares the latest synchronized video/overlay
pair. Native item changes schedule Quick rendering without an additional surface update.
Frame updates schedule Quick rendering without also repainting the surrounding
QWidget; the widget background only needs painting when no frame is present. Catalog/filter callbacks and text shaping never run inside updatePaintNode.
Only prepared data is consumed by native scene graph callbacks. Hover highlighting
is an independent full-opacity layer; diagnostic HUD and controls are widgets above
the Quick surface. The registered QML root survives QQuickWidget window recreation
when moving the viewer into fullscreen. Cleanup submits an empty frame and synchronizes an initialized scene graph
before hiding, so Qt deletes native image nodes while their Python texture adapters are still alive. The temporary
catalog export renderer is explicitly destroyed after cleanup, including on errors; that command has no event loop
to process deferred widget destruction.

QQuickWidget preserves widget stacking and costs an extra composition pass; it does
not have Quick's threaded render loop. Window embedding is therefore an explicit
integration tradeoff, not a claim that Python preparation has moved off the GUI thread.

`FrameImageRenderer` uses the same Quick surface for image/video export, in a window
marked `WA_DontShowOnScreen`. It renders at native source resolution, compensates for
desktop device-pixel ratio, and reads back the framebuffer for the encoder. Export runs
on the GUI thread and owns its rendering resources until completion or cancellation.

## Presentation Assembly

`SceneFramePresenter` adapts `FrameData` and optional `OverlayData` to video-player data types.

Responsibilities:

- Convert `FrameData` into `VideoFrame`.
- Accept indexed overlay selection from offline playback/export, or apply live reuse through `OverlayPersistencePolicy`.
- Merge overlay metadata and presentation metadata, including overlay opacity.
- Retain one `CachedSceneOverlay` while source ID, sample frame identity, and Scene identity match.
  A missing/replaced sample, replaced filter adapter, or cleanup releases it.
- Use that overlay's cached filtered Scene for the entity inspector.
- Attach the active `SceneRenderCatalog` through both a direct catalog value and a catalog provider callback.

Published Scenes are snapshots: content changes require a replacement Scene, not mutation
of a previously delivered object.
The catalog provider holds a weak presenter reference so the retained cache does not form
a reference cycle. Existing display frames can outlive the presenter using their original catalog.

The provider callback is important. It lets an already-created `CachedSceneOverlay` see a newly attached catalog before the next frame object is built. This is covered by `test_presenter_updates_existing_overlay_catalog_before_next_frame`.

Closed entity inspectors retain only the latest pending update and materialize it when shown.
Visible inspectors reuse rows for an unchanged filtered Scene, while updating the frame label;
detail HTML is built only for expanded rows.

## Cached Scene Overlay

`CachedSceneOverlay` is the production overlay adapter for one source `Scene` identity. It implements three video-player-facing protocols:

- direct drawing preparation through `prepare_drawing(context, buffer)`;
- hover hit-testing through `hit_test(...)` and `get_hit_by_id(...)`;
- drawing preparation metrics through `latest_drawing_preparation_metrics()`.

It owns two main caches.

Filtered scene cache:

```text
(id(filter_config) or None, filter_state_snapshot or None)
```

Prepared drawing cache:

```text
(
  filtered_scene_key,
  (context.width, context.height, drawing_settings),
  scene_render_catalog.rendering_identity,
)
```

Each cache owns one source Scene for its lifetime. A new Scene gets a new cache. Existing caches rebuild when filter configuration/state, target dimensions, backend, DPI/DPR, viewport bounds, or catalog identity changes. Visibility changes replace the effective catalog and invalidate prepared drawing. The rendering identity contains the compiler semantic revision and a canonical semantic hash. Drawing changes invalidate prepared output; descriptive metadata and mapping-key reordering do not. The separate content hash tracks document revisions for reload and persistence.

Hover targets are built from the same filtered scene and latest-observation policy as drawing preparation. Hit testing chooses the smallest bounding box containing the normalized cursor position, which keeps nested or overlapping targets predictable. Pinned hover selections are re-resolved by entity id against the current filtered scene.

## Scene Render Catalog

`SceneRenderCatalog` is the compiled, runtime form of one catalog JSON document.

It contains:

- `catalog_id` and `content_hash` for identity and cache invalidation;
- compiled reusable templates as a `RenderCatalog`;
- required fallback recipes for `unclassified` and `classified` entities;
- classification recipes keyed by classification type.

Entity rendering is latest-observation based:

1. Skip entities without observations.
2. Read `entity.latest_observation`.
3. Read `observation.primary_classification`.
4. If no classification exists, use the `unclassified` fallback recipe.
5. If a classification-specific recipe exists, use it.
6. Otherwise use the `classified` fallback recipe.

Relation rendering follows entity rendering. A relation recipe is selected by `EntityRelation.type`, both endpoints are
resolved from the filtered Scene, and the relation is skipped when its recipe, either endpoint, or either endpoint's
latest observation is missing. This makes entity filtering apply to relations without a separate relation-filtering
policy.

Six built-in catalogs ship in `catalog_definitions/`, listed in the order of `BUILT_IN_CATALOG_PATHS`. Their looks are
summarized in the [README](../../README.md#change-how-overlays-look); each JSON file is the reference for its own
templates and recipes, and `ax-devil catalog list` lists them.

Recipe selection is catalog policy. Decoder classification strings are evidence, not UI routing logic.

## Overlay Visibility

**Details** beside the media tools Catalog button controls which semantic components a view draws; the user-facing
behavior is in [Usage](../usage.md#overlay-details). Each live view and offline lane has its own immutable
`OverlayVisibility`; offline playlist viewers retain it by original lane position. Unused templates and permanently
hidden components do not enable controls.

`visibility.py` owns the eight typed `OverlayFeature` identifiers, their labels and descriptions: `outlines`, `ids`,
`class_names`, `confidence`, `speed`, `movement`, `attributes`, and `relations`. A template or step may declare
`"feature": "confidence"`. Both gates apply when a tagged step invokes a tagged template. Untagged drawings remain
permitted. Catalog `visible: false` and data-dependent `enabled` conditions still apply. Class names means explicit
text, not the colors used to distinguish classes. Composite labels must separate components into independently
tagged steps; arbitrary individual label runs cannot be gated.

Visibility is applied when the catalog is compiled, never while frames render. The complete document is validated first,
so hidden components still have to be valid. On a settings change the selection compiles a variant from the already
validated source without rereading the file; the compiler drops disabled components and any inputs only they read, so
hidden output costs nothing per frame. Each catalog revision keeps its most recently used variants compiled. A failed
specialization or file reload keeps the last good catalog in use. A variant's rendering identity includes its hidden
features, so changing visibility invalidates prepared drawing but not the filtered Scene or hover index. Export uses the
variant active when it starts. Catalog previews and CLI checks always show every feature.

## Catalog JSON And Compilation

The built-in catalogs use schema version 3. Older versions are rejected explicitly;
there is no legacy runtime or migration layer. Built-in catalogs are read from the package in place. User catalogs
must use v3 to be selected. A failed load leaves the last good active catalog usable.

Ownership within `template_runtime/`:

- `definitions.py` describes closed value types, named operation inputs/results, primitive fields, and execution limits.
  Allowed values are data on `ValueType`: numeric minimum, exclusive minimum and maximum, maximum text length, and list
  item counts, named as in JSON Schema. A record's bounds live on its field types, so a stroke width is a nonnegative
  length while an offset is an unbounded one. Validation, the checks generated for each row, and `ax-devil catalog reference` all read
  these bounds; `RULES` holds the few checks bounds cannot express (point counts of nonempty polygons and polylines,
  increasing color stops). `authoring_definitions()` exposes serializable authoring data, bounds included.
- `schema.py` derives structural JSON Schema from those definitions. The packaged schema is checked against it in tests.
  Expression and step alternatives use `anyOf` to stop validation at the first match. Their JSON types, reserved
  keys, and distinct operation/primitive names keep the alternatives mutually exclusive.
- `compiler.py` checks scope, references, types, parameter defaults, nullability, calculated-value dependencies,
  template cycles, and worst-case expansion. It resolves calls into immutable expression and program objects.
- `expressions.py` holds typed compile-time expression metadata.
- `program.py` owns template plans and the standalone mapping-based drawing entry point. Standalone
  functions compile on first use; nested templates only contribute inlined plans.
- `executable.py` lowers validated plans to row-column Python functions: one call evaluates a program for every
  row of a table. Values are Python values shared by every row or NumPy arrays with one entry per row; records
  and lists stay structural (one local per field), nulls are presence masks with typed placeholders, and the
  value type carried by each located plan node selects the representation. Nested template bodies are inlined.
  Catalog text is bound data, never executable source. The same lowering runs one shared-value row for
  standalone template rendering and compile-time constant folding, where values stay plain Python values.
- `kernels.py` holds the row-column runtime: failure reporting, per-row selection, vectorized arrow and color
  ramp operations, per-row text formatting, and deferred primitive emission.
- `catalog.py` routes entities and relations to recipes and builds a lazily extracted column table per recipe.
  Only demanded Scene fields are read, once per column per frame, and whole columns are validated at once.
  Finished Scene recipes retain only their update function, not compiler plans.
- `quick/batching.py` owns reusable NumPy vertex scratch storage and ordered geometry publication.

File loading and in-memory compilation use the same validation boundary. Parsing rejects duplicate JSON keys;
structural and semantic validation complete before activation. A document whose content hash matches the packaged
default skips the jsonschema pass, about 115 ms, because tests validate the packaged catalog against the schema; it
still compiles. Compilation never occurs on the frame draw path.

References are objects such as `{"ref": ["parameters", "geometry", "x"]}`. Strings, including `$` prefixes, are
literal text. Calls use named arguments: `{"call": "div", "args": {"numerator": 1, "denominator": 2}}`.
Reserved-key data objects require `{"literal": ...}`. Records and arrays may contain expressions.

Templates declare typed parameters with required status or a literal default. A parameter may also declare allowed
values: `minimum`, `exclusive_minimum` and `maximum` for numbers and lengths (a length applies them to its value), or
`max_length` for text. Constant inputs are checked where they are written, each input on its own so errors are
located at it; inputs that vary per object are checked per row. Recipes, templates, parameters and steps may carry a
descriptive `label`, excluded from rendering identity like `description`. A step with `visible: false` is validated
but draws nothing. `parameters` and calculated `values`
have distinct namespaces. Templates see only their parameters and the immutable render context. There is no
arbitrary Python attribute traversal or implicit caller scope. Calculations resolve by dependency, not JSON order.
Each demanded local value is memoized once per invocation. Template invocations have independent slots.

Compilation specializes template calls for their known constant parameters, preserving signed zero in specialization
keys. It lowers arithmetic and common geometry to array expressions over row columns, or plain Python arithmetic
when every operand is shared. Primitive output becomes one deferred batch per catalog step; after evaluation, the
rows that completed without failures are submitted to the target. External Scene references are memoized per
invocation and shared by every step that demands them, never across frames. Dynamic arithmetic still checks finite
results and applicable value constraints, per row. Shared-value (one-row) evaluation keeps Python number semantics;
per-row columns are float64. Python code is generated once at compilation, using compiler-owned syntax and bound
constants; no JIT or alternate evaluator is used.

Hover indexes contain bounds and entity references. HTML is formatted only for the selected entity and reused within
that overlay/filter revision; inspecting empty space does not format cards for every visible entity.

`enabled`, `if`, and `coalesce` are lazy per row. A shared condition executes only the chosen branch. A per-row
condition evaluates both branches over row masks and a failed check only fails the active rows that demanded it,
so an unused branch cannot fail a row. Memoized values are computed for every row and report failures where rows
demand them. Every branch is structurally/type checked, while unused arithmetic is not executed by constant folding. Explicit `is_present` and `is_not_empty` reference guards narrow nullability. Classification
routing itself also establishes that classified recipes have a classification. Recipe/step arrays retain draw order.

Compile-time expansion limits count call sites and primitive steps over the acyclic template graph without materializing
its expansion. Limits and supported types are available through the authoring definitions.

## Scene Inputs And Runtime Errors

Entity recipes receive `scene.entity`, `scene.observation`, `scene.classification`, explicitly declared `bindings`,
and `context`. Scene projections contain plain typed data: boxes `{x,y,w,h}`, points `{x,y}`, vectors
`{vx,vy}`, classification type/score, entity id, and optional motion state (`moving`, `stationary` or `unknown`).
The motion state is absent when the entity has none. The built-in catalog maps it to
`▶`, `Ⅱ` and `?` with `lookup_text`, so the symbols stay in the catalog. Binding values are resolved and validated
only when requested. Color bindings expose `color` and `score`; bool/number bindings return their optional value.

Relation recipes receive `scene.relation.type` and `scene.source`/`scene.target`, each containing the endpoint entity id
and observation geometry. Endpoints come from the filtered Scene; relations without two observed endpoints are skipped.
Entity recipes remain before relations, and relations retain their deterministic semantic order.

`render_scene(..., diagnostics=...)` evaluates each recipe once for all its rows. A row that fails a located check
emits none of that recipe's output; other rows and recipes are unaffected. Diagnostics contain a stable code,
document path, message and recipe id, reported once per catalog, code and location. Compiled catalogs hold no mutable
diagnostic state. `CachedSceneOverlay.diagnostics` exposes current failures; a presenter-owned `ReportedCatalogErrors`
remembers the 256 most recently seen errors, so repeats are not logged again as new frame adapters are created, and new
errors evict the least recent instead of being silenced.

Numeric inputs and computed results must be finite. Typed lengths use `px`, `image_width`, `image_height`, or `image_min`;
resolution uses the current target, not source-video dimensions. Outside-image positions remain valid. Dimensions and
stroke widths are nonnegative, zero-area boxes emit nothing, and nonempty polygons require at least three points.
Geometry operations preserve subpixel coordinates and account for non-square targets.

`lookup_text` maps `text` through a `mapping` list of `{key, text}` entries with unique keys. Exact, case-sensitive
matches return the entry's text; unmatched text returns `default`; absent text stays absent.

`pick_color` picks one of a `palette` of colors for `text` by its CRC-32, so an id keeps its color in every frame and
every run (Python's `hash()` is salted per process). Different ids can share a color; a longer palette makes that rarer.

## Drawing Preparation And Submission

`engine/drawing.py` defines the batch `DrawingTarget`, `Paint` columns, `DrawingStyle` and shared text shaping.
`engine/quick/preparation.py` implements the single production target and its immutable submission data.

- Catalog calls use normalized image coordinates; outside-image positions remain valid.
- Stroke width, font size and circle radius scale from `min(width, height)`.
- The target immediately resolves coordinates into logical surface pixels and prepares geometry/text.
- `RenderContext` defines catalog units. `DrawingSettings` separately carries the exact surface size,
  scale, DPI, device-pixel ratio, graphics backend and overlay-local viewport. Export uses source-pixel
  catalog context and DPR-adjusted logical surface geometry, preserving native output dimensions.
- Zero-width strokes disable the pen; no hidden minimum width or font-size offset is applied.
- Text uses independent shaped lines, explicit line breaks and shared anchors; polygons use odd-even fill.
- Labels lay out their runs on one line, leave out runs with neither text nor a bar, and anchor their background
  rectangle. A bar is two and a half font sizes long; its fill is kept to hundredths so similar values share a sprite.
- Dashed strokes alternate dashes and gaps of three line widths (`Path.qml`).

`VideoFrameWithOverlays.prepare_overlays(context, buffer)` retrieves prepared output and resolves opacity.
`VideoFrameRenderer` binds it to the retained surface along with the video image and hover highlight.
`FrameImageRenderer` shares the same preparation and submission path in a non-visible Quick surface.

## Render Catalog Storage And UI

`SceneRenderCatalogStore` owns user-store file lifecycle and the choice of default catalog.

Two terms are kept apart:

- The **built-in catalogs** are the packaged `catalog_definitions/*.json` files named in `BUILT_IN_CATALOG_PATHS`.
  They are read in place from the installed package, never copied into or written to the catalog store, and cannot be
  deleted; users copy one to change it.
- The **default catalog** is the catalog new selections start from. It is the default built-in catalog, Standard
  (`BUILT_IN_CATALOG_PATH`), until the user picks another one with **Use as default**.

Storage behavior:

- User catalogs live under `~/.ax_devil/render_catalogs/` by default. Every JSON file there is a user catalog,
  whatever its name, so the app never overwrites a user file. The listing puts the built-in catalogs first.
- `create_catalog(name, base_catalog_path)` copies an existing catalog, rewrites metadata id/name, validates before writing, and creates a unique slug filename.
- `set_default_catalog(path)` stores the chosen user catalog as `./<file name>`, or `built-in:<file name>` for another
  built-in catalog, in `default_catalog.txt` in the same directory; choosing Standard removes that file. The listing
  reports Standard as default when no choice is stored, the stored choice cannot be read, or the chosen file is no
  longer listed.
  Deleting the default catalog clears the choice.

`ConfigManager` supports `storage.render_catalogs_dir` to override the storage path.

`SceneRenderCatalogManager` is the runtime owner for shared catalog state. `Application.run()` creates one manager after config and storage setup, initializes it once, and passes it through `MainWindow`, `WorkspaceSession`, viewer factories, live/offline viewers, media panels, selectors, and the catalog viewer. The manager owns:

- the shared `SceneRenderCatalogStore`;
- the shared `SceneRenderCatalogLoader`;
- the latest cheap metadata listing, including the default catalog path;
- compiled catalog reuse by path and file fingerprint;
- catalog status and non-fatal catalog file errors;
- watching the catalog files, so changes made outside the app reach every view showing them.

`SceneRenderCatalogSelection` owns one consumer's active catalog path, active compiled `SceneRenderCatalog`, active status, and reload policy. The active path is the catalog the user chose; the active catalog is the last one that compiled, which after a failure may be an older revision or another file. Every load attempt of the active path (select, reload, startup, and the validation done by `apply_to_all()` and `use_as_default()`) records a failure, and only a successful compile clears it, so `active_catalog_loaded()` is true exactly when the latest attempt compiled. A failed listing refresh during reload is reported but is not a load failure. `selectionChanged` fires after every change to the path, catalog, load state or status, including failed loads and every listing change. `SceneRenderCatalogManager` answers `can_apply_to_all(path)` (listed and compiles) and `can_use_as_default(path)` (additionally not the chosen default, so the built-in catalog standing in for a broken chosen default can be chosen again); the selector and the catalog viewer both use these. `activeCatalogChanged` fires only when the catalog in use changes, for presenters. Live viewers create one selection for the viewer and share it between the live controller and that viewer's media tools panel. Offline multi-lane runtime creates one selection per lane, so each lane can choose a different catalog while still using the shared manager for listing and compiled reuse.

Catalog choices have three scopes, and only the default is remembered across restarts:

| Scope | Call | Effect |
|---|---|---|
| One view | `SceneRenderCatalogSelection.select_catalog(path)` | That viewer or lane switches. |
| All open views | `SceneRenderCatalogManager.apply_to_all(path)` | The catalog is compiled first; if that fails nothing changes and the error is raised. Otherwise every existing selection switches through `catalogAppliedToAll`. |
| New views | `SceneRenderCatalogManager.set_default_catalog(path)` | The catalog is compiled first; if that fails the previous default stays and the error is raised. Otherwise selections created later start from it. Open views keep their catalog. |

A new selection starts from the manager's default catalog. If that catalog fails to compile, the selection uses the
default built-in catalog and reports why in its status.

`SceneRenderCatalogStore.list_catalogs_with_errors()` is metadata discovery only. It reads JSON metadata and reports
unreadable or metadata-invalid files, but it does not schema-validate or compile every catalog during listing. Selection
and reload validate the selected file before normalization and compilation. The store also performs full JSON Schema
validation before writing created catalog files. Writes replace the destination atomically after validation,
keeping the previous file intact if writing or replacement fails.

The media tools panel contains `SceneRenderCatalogSelector`, but the selector is only UI over a `SceneRenderCatalogSelection` and the shared manager listing. It exposes a **View** button that opens the catalog viewer on the active catalog, a reload button, and a `⋯` menu with **Apply to all** and **Use as default**, which call the selection's `apply_to_all()` and `use_as_default()` and are enabled by the manager's `can_apply_to_all()` and `can_use_as_default()` for the active path while the selection's latest load of it compiled. Dropdown labels come from `SceneRenderCatalogListing.label(path)`: the catalog name marked `(default)` for the default, or the file name marked `(invalid)` or `(missing)` for an unlisted active path. Selecting a catalog calls `SceneRenderCatalogSelection.select_catalog(...)`. Reload calls `SceneRenderCatalogSelection.reload_active_catalog()`, which refreshes the shared listing and then recompiles the active path, so a repaired file stops showing as invalid. The selector redraws its whole state from the selection and the listing on `selectionChanged`, and shows the selection's status. The selector does not read catalog files, compile catalogs, or refresh/reload automatically before opening the dropdown.

If a selection's active catalog path is missing or invalid, that selection keeps its last successfully compiled catalog and publishes an error status. Status is derived in order: listed invalid, listed missing, the recorded load error, then "Selected catalog". A listing refresh therefore never hides a load error. Selectors show their selection status and keep the selected path visible so the user can reload, switch catalogs, or open the viewer.

Catalog files are edited outside the app, usually by an AI agent following `.agents/skills/render-catalog/SKILL.md`.
The manager watches every listed catalog file, invalid ones included, and the directories holding them. After 150 ms of
quiet it compares file fingerprints; for each file that changed, was replaced (as many editors save) or was removed, it
refreshes the listing and emits `catalogFileChanged(path)`. Every selection whose active path is that file
loads the new version, keeping the last one in use if it does not load, and other selections are untouched. The
catalog viewer (`ax_devil.modules.catalog_viewer`), opened from **View → Render Catalogs** or a selector's **View**
button, draws a catalog on example sheets and redraws on every change; it and the `ax-devil catalog` commands are
described in [Catalog Viewer](catalog-viewer.md).

Live viewer wiring:

```text
SceneRenderCatalogSelection.activeCatalogChanged
  -> StreamMediaController.attach_scene_render_catalog(...)
  -> SceneFramePresenter.attach_scene_render_catalog(...)
  -> refresh via _on_filter_changed()
```

Offline viewer wiring:

```text
lane SceneRenderCatalogSelection.activeCatalogChanged
  -> that lane SceneFramePresenter.attach_scene_render_catalog(...)
  -> that lane FrameDisplay.refresh_overlays()
```

## Filtering, Inspection, And Hover

`CachedSceneOverlay` owns the filtered Scene projection used by rendering, hover, and inspection. `SceneFramePresenter`
asks that cache for the inspector Scene while assembling the frame; later drawing preparation and hover lookup reuse the
same projection while the filter state is unchanged. The cache owns the original source Scene;
`VideoOverlayData` holds its bound generator and does not carry a duplicate opaque payload.

If filtering raises while the presenter prepares inspector data, it logs the failure and publishes the unfiltered Scene
so the viewer still works.

Hover cards are generated lazily from `scene.inspection.build_entity_hover_html(entity)` when requested. The video renderer only receives `HoverHit` with a target id, normalized bounds, and card HTML.

## Rendering diagnostics

`CachedSceneOverlay` reports drawing preparation metrics (filtering, generation, cache reuse, workload, rebuild
reasons) through the overlay metrics protocol, and `VideoFrameRenderer` records one atomic paint sample per Quick GUI
preparation. The dashboard, the exact timing boundaries, spike inspection and the export format are documented in
[the diagnostics README](../../src/ax_devil/modules/diagnostics/README.md#measurement-definitions).

## Extension Points

Common future changes should land in these places:

- Change a shipped look: edit the built-in catalog in `catalog_definitions/`, update `catalog.schema.json` only if the shape changes, and extend catalog/runtime tests.
- Add a new classification route: add or change a classification recipe selector in the catalog JSON.
- Expose more entity or observation data to recipes: add typed recipe inputs or binding factories in `catalog.py`, then update schema and compiler tests.
- Add a new JSON primitive: extend `DrawingTarget` and `DrawingBuffer`, declare its fields in `PRIMITIVES`, implement direct emission in `template_runtime/executable.py`, regenerate the schema, and add template runtime tests.
- Add a user-selectable detail: extend `OverlayFeature`, tag the catalog components, regenerate the schema and extend specialization tests.

## Invariants

Rendering rules live with the other domain invariants in [Domain Invariants](../domain/invariants.md#rendering),
together with its Coordinates and Display Rendering sections.

## Current Limitations

- `get_built_in_scene_render_catalog()` compiles the packaged catalog once per process for tests and for overlays
  created without a selection. Normal app startup uses the app-owned `SceneRenderCatalogManager` instead.
- Catalog JSON is treated as machine-authored configuration; Python runtime, schema, and tests are the readable source of truth for behavior.

## Decisions

Revisit these only with new measurements that change the tradeoff.

- **Catalog execution stays compiled Python.** Recipes compile to Python update functions that evaluate each recipe
  once over NumPy row columns. A C++ extension was measured about three times faster, but is not worth maintaining catalog
  semantics twice and shipping binary wheels per platform and Python ABI.
- **Text uses Qt's native glyph rendering.** Text items are matched by content, so labels that appear or disappear do
  not rebuild other glyph nodes. A bitmap label atlas looks worse.
- **Labels share one `LabelLayer`** that keeps their textures, so returning content such as changing scores is not
  repainted and uploaded again.
- **Garbage collection runs on the GUI thread over a frozen startup heap.** `app.py` freezes everything alive after
  startup and collects from a GUI-thread timer; collecting the unfrozen startup heap pauses about 13 ms. Do not change
  global GC thresholds to improve benchmarks.

## Performance

The GUI thread is the limit; video decoding costs little (about 0.09 cores for 1080p25). With 250 moving objects per
lane on a 1080p25 recording, two lanes play at full rate and four lanes saturate one core, where playback stays in
real time by showing fewer frames. Per lane, catalog evaluation takes about 2.4 ms and text preparation about 0.9 ms.
A first open of a large ADF file is dominated by parsing; later opens use the cache.

These figures come from the full app playing a 45 s 1080p25 H.264 clip (8 Mbit/s) in a playlist of 1, 2 or 4 lanes,
each with an ADF overlay of 250 moving objects, on Qt 6.10.2 with an RTX 5080; a cgroup CPU quota stood in for a
slower machine. As a baseline for `tools/benchmark_catalog_lanes.py --lanes 4 --entities 250` on that machine, one
round costs about 24 ms of catalog evaluation and drawing preparation, 4.1 ms of item binding, 0.8 ms of scene graph
sync and 3.6 ms of render submission, for 36 ms of process CPU. Measure changes with the
[rendering benchmarks](../runbooks/testing.md#rendering-benchmarks).

## Open Work

### Catalog language

- Add ellipse, arc, pie, chord, path and image primitives to catalog JSON.
- Let bindings read attributes beyond the primary classification.
