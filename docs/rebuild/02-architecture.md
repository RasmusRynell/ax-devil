# 02 Architecture

Current shape: `docs/architecture/overview.md`, `docs/architecture/module-map.md`,
`docs/architecture/ui-framework.md`, `docs/architecture/draw-system.md`,
`docs/architecture/workspace.md`. This file says which parts carry over and which seams to redraw.

## Principles

- **MUST** organize code by owning concept and mirror that ownership in tests
  (`docs/architecture/module-map.md#placement-rules`).
- **MUST** keep UI a projection of state owned elsewhere (`docs/domain/invariants.md#ui-lifecycle`).
- **MUST** put shared display or behavior decisions on the owning domain object; typed closed sets over raw strings.
- **SHOULD** make modules deep (`.agents/skills/review/SKILL.md#1-deep-modules`).

## Layers and dependency direction

Dependencies point down only.

| # | Layer | Owns | Current |
|---|-------|------|---------|
| 1 | Foundation | Media Time, frame identity, cancellation, atomic writes, source snapshots, user-facing error value, logger factory | `src/ax_devil/core/`, `settings/logging_config.py` |
| 2 | Domain and contracts | Scene model and its serialization schema, Sample Item encoding, decoder contracts, filtering, inspection queries, timestamp matching and sync engines, Workspace model, file format, intake, bookmarks | `scene/`, `filtering/`, `synchronization/engine.py`, `workspace/core/` |
| 3 | Runtime services | Settings service (load, validate, save, notify), workspace store, lifecycle, recovery copy and row projection, sources and transports, file providers and stores, frame cache, jobs, file watching, discovery, thumbnails, plugin runtime, catalog document service | `settings/`, `workspace/ui/workspace_store.py`, `workspace/ui/workspace_lifecycle.py`, `workspace/ui/browser_projection.py`, `data_sources/`, `background_jobs/`, `file_watching/`, `device_discovery/`, `plugin_*`, `scene/rendering/catalog_*` |
| 3a | Event-loop adapters | Change notifications and UI-thread delivery for layer 3; no other logic | `synchronization/qt_adapter.py`, Qt signals on services |
| 4 | UI support | Tokens, appearance, icons, component kit, shortcut registry, viewer-host contract | `chrome/`, `shortcuts/`, `workspace/ui/viewer_widget.py` |
| 5 | Generic display | Display shell, viewport math, quad painting, hover chip, controls; no Scene knowledge | `video_player/` |
| 6 | Workflows | Live and offline viewers, overlay presentation (catalog compile and evaluation, filters, layout, picking: `frontend/src/present/`, D22), export, media tools, catalog viewer, object inspection | `video_viewer/`, `catalog_viewer/`, `object_inspection/` |
| 7 | Shell | Application composition: window, menus, sidebar views that render rows, split host, dialogs | `application_shell/`, `workspace/ui/` |
| 8 | Entry and bootstrap | CLI, app bootstrap: creates services and passes them down | `src/ax_devil/cli.py`, `src/ax_devil/app.py` |

- **MUST** keep layers 1–3 free of the UI toolkit; event-loop adapters are layer 3a, never inside 3, so the CLI and
  workspace lifecycle tests need no toolkit. *Current:* only `workspace/core` is enforced Qt-free; the store, sources, sync adapters and settings emit Qt signals. **change**.
- **MUST** let domain code take values, never read settings; the settings service is wired at bootstrap.
  *Current:* settings sit below everything as a process-wide singleton. **change**.
- **MUST** enforce the boundaries automatically, as [the architecture check](../../scripts/check-architecture.cjs) does.

## Seams to keep

| Seam | Why | Source |
|------|-----|--------|
| Item → Content → runtime source | Saving, re-resolution and viewer lifetime stay independent | `docs/architecture/workspace.md#layers` |
| Workspace core vs UI | CLI and tests build items without a UI | same |
| Scene as the one exchange format | A decoder plugs into filtering, inspection and rendering at once | `docs/domain/invariants.md#scene-model` |
| Presentation (Sample Item to Marks) vs painting (Marks to pixels) | Display and export run the same presentation code; the catalog language evolves without touching the GPU path | `docs/architecture/overview.md#overlays` |
| Three source capabilities (frame push, live overlay push, offline overlay lookup by frame identity) | Live and offline converge on one presenter | `docs/architecture/overview.md#data-sources` |
| Pure sync engine plus adapter | Deterministic tests of timing rules | `docs/architecture/overview.md#synchronization` |
| Viewer host vs viewer contract | New center-pane tools without host changes | `docs/architecture/ui-framework.md#viewer-widgets` |
| One store, one row projection | Views never disagree and stay thin | `docs/architecture/overview.md#workspace-state` |
| Catalog documents served by the backend, compiled and selected per lane in the frontend | Hot reload and Apply to all without lanes reading files | `docs/architecture/overview.md#overlays` |
| Owner-scoped background jobs | One call cancels everything an owner started | `src/ax_devil/modules/background_jobs/README.md` |
| Plugin registry with recorded failures | Broken plugins never stop the app | `docs/domain/invariants.md#plugin-system` |

## Seams to redraw

| Current | Evidence | Recommendation | Status |
|---------|----------|----------------|--------|
| `video_viewer` ↔ `workspace` mutual dependency | `docs/architecture/overview.md#layers` | Viewer-host contract in layer 4; viewer factories registered by Content kind at bootstrap | **change** |
| Resolver contract requires a toolkit widget and a CLI command object | `docs/architecture/workspace.md#playlist-resolver-contract` | Declared settings schema rendered by the host and the CLI (D4) | **change** |
| Decoders return host data-source factories | `docs/architecture/overview.md#layers` | Decoders yield per-frame Scenes or records plus declared metadata ([05](05-formats-and-extensions.md#decoder-contract)); the host builds and owns the indexed store and lookup; no whole-file Scene list in memory | **change** |
| Process-wide config and settings singletons | Tests reset shared config after each test (`docs/runbooks/testing.md#tests-never-touch-the-users-files`) | Settings service passed from bootstrap | **change** |
| Interpreter-selecting launcher and locked plugin environment | `docs/domain/invariants.md#plugin-system` | Replace with the plugin runtime (D29): a plugin's `command` owns its environment, keeping its guarantees | **drop** mechanism |
| Catalog recipes compiled to Python over NumPy columns | `src/ax_devil/modules/scene/rendering/README.md#rules` | Schema v4 (D25) compiled and evaluated in the frontend over Sample Item columns | **change** language and engine |
| Cyclic GC frozen and collected on the GUI thread | `docs/domain/invariants.md#ui-lifecycle` | Guarantee instead: no frame-time pauses from memory management; resources destroyed on the executor they require ([03](03-runtime.md)) | **drop** mechanism |
| All overlay preparation on the GUI thread (embedded Quick widget has no threaded render loop) | `src/ax_devil/modules/video_player/README.md#quick-surface` | Per lane on the browser main thread over immutable Sample Items (D22); the backend does only the work shared across lanes | **change** |

## Deep modules

Each gets a small interface and its own tests.

| Module | Interface | Current |
|--------|-----------|---------|
| Timestamp matching and sticky selection | Samples + policy + frame identity → sample and match kind | `synchronization/timestamp_matching.py`, `video_viewer/overlay_persistence.py` |
| Live stream sync | Frames and overlays with capture times → synchronized pairs | `synchronization/engine.py` |
| Scene file provider and stores | File + decoder → Scene by frame identity, cached by full identity | `data_sources/file_data_provider/` |
| Frame source with cache pool | Video → frame by position under a shared allowance | `data_sources/file_frame_source.py` |
| Catalog compiler and evaluator | Catalog document → compiled catalog; Sample Item and Lane Style → Marks (target: `frontend/src/present/`) | `scene/rendering/template_runtime/` |
| Layout and painting | Marks → quads and glyph atlas → pixels, per lane | `video_player/engine/quick/preparation.py` |
| Workspace model and file format | Read/write workspace; resolve items | `workspace/core/` |
| Workspace store and lifecycle | Mutate, notify once, save, open, recover | `workspace/ui/workspace_store.py`, `workspace/ui/workspace_lifecycle.py` |
| Row projection | Workspace + resolutions + open viewers → rows with actions | `workspace/ui/browser_projection.py` |
| Viewport state | Zoom/pan state machine and fit math | `video_player/engine/viewport_state.py` |
| Network scan | Ranges + optional logins → devices, cancellable | `device_discovery/network_scan.py` |
| Export job | Frozen lane inputs → encoded file, atomic, cancellable | `video_viewer/export/export_job.py` |

Thin by design: sidebar views, dialogs, display shell, transport bars, CLI commands (they only build items).

Data flows are stack-neutral in the sources: `docs/architecture/overview.md#synchronization`,
`docs/architecture/overview.md#live-source-composition`,
`docs/architecture/overview.md#overlays`, resolution
(`docs/architecture/workspace.md#resolution`).
