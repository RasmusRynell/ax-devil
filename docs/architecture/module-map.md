# Module Map

Use this guide when deciding where current code belongs under `src/ax_devil/`.

## Placement Rules

- Put product code under `src/ax_devil/modules/` unless it is a top-level entry point, a built-in plugin bundle, or a packaged resource.
- Start from the owning concept, not the current caller.
- Keep widgets thin. Controllers, domain objects, and module-owned services hold behavior.
- Put shared labels, descriptions, and ownership decisions on the domain object that owns the concept.
- Mirror ownership in tests under `tests/modules/`.

## Module Owners

| Code Area | Owner |
|-----------|-------|
| App bootstrap, config wiring, logging setup, plugin loading | `src/ax_devil/app.py` |
| CLI commands and maintenance entry points | `src/ax_devil/cli.py` |
| Main window menus, app-wide dialogs (including Settings and Quick Setup), diagnostics window launch | `src/ax_devil/modules/application_shell/` |
| Workspace model: the Workspace, Workspace Items and their resolution, file format, kept workspace and recent workspaces, content, intake, item information (Qt-free) | `src/ax_devil/modules/workspace/core/` |
| Workspace interface: `WorkspaceStore`, plugin-backed background resolution, browser rows, add-content and rename dialogs, workspace lifecycle and prompts, split-view hosting, `ViewerWidget`, viewer factory, session/controller | `src/ax_devil/modules/workspace/ui/` |
| Live/offline viewer workflows, media tools, offline entry opening (`EntryOpening`, `EntryMedia`), `OfflineSession`, `OfflineLane`, source pooling, Scene presentation | `src/ax_devil/modules/video_viewer/` |
| `FrameDisplay`, `FrameViewport`, control panels, viewport behavior, video transforms, drawing contract and preparation | `src/ax_devil/modules/video_player/` |
| Scene model, decoder helpers, inspection, filtering, draw recipes, `CachedSceneOverlay`, Scene-to-drawing preparation | `src/ax_devil/modules/scene/` |
| Render catalog viewer: example sheets, the live viewer window, the `ax-devil catalog` commands and the generated language reference | `src/ax_devil/modules/catalog_viewer/` |
| Frame sources, overlay sources, file providers, transport/runtime source plumbing | `src/ax_devil/modules/data_sources/` |
| Pure sync engines, timestamp matching policy, and Qt sync adapters | `src/ax_devil/modules/synchronization/` |
| Session filtering, shared filter configs, state, predicates, and whole-file history filtering | `src/ax_devil/modules/filtering/` |
| Plugin discovery, registry, contracts, handler lookup APIs, installation validation | `src/ax_devil/modules/plugin_system/` |
| Locked plugin installation projects and install/update/remove commands | `src/ax_devil/modules/plugin_installation/` |
| Selecting the application interpreter before Qt imports; shared CLI options | `src/ax_devil/launcher.py`, `src/ax_devil/cli_options.py` |
| Config, settings state and preference values, logging, paths | `src/ax_devil/modules/settings/` |
| Shortcut definitions, the manager that installs them, override persistence, and the shortcuts dialog | `src/ax_devil/modules/shortcuts/` |
| Shared window/dialog chrome, design tokens (type scale, spacing, radii, heights), icons, menu buttons, browse buttons and key chips, screen-aware geometry, content scrolling, temporary dialog lifetime, and form layouts | `src/ax_devil/modules/chrome/` |
| Diagnostics windows, metrics, exception reporting | `src/ax_devil/modules/diagnostics/` |
| Cache services | `src/ax_devil/modules/cache/` |

## Live Sources And Add-Content Placement

`data_sources/live/` groups RTSP, MQTT, and DataHub transport implementations and discovery. Shared source contracts
remain in `data_sources/base.py`; file sources and providers remain outside `live/`.

`workspace/ui/add_content/` groups the three add-content dialogs and their analytics discovery and playlist-selection
helpers. The Workspace, its items, content descriptions, and intake validation live in `workspace/core/` because they
also serve the CLI and any future UI. `workspace/ui` is the Qt interface on top; the Qt-free rule for `core/` is in
[invariants](../domain/invariants.md#content-model), and the split and its planned direction are described in
[Workspace](workspace.md). Tests mirror `core/`, `ui/`, and `ui/add_content/`.

## Outside `modules/`

- `src/ax_devil/core/` contains small foundational types and pure policies, such as playback speed, that do not
  belong to a concept-owned module.
- `src/ax_devil/plugins/` contains built-in decoder and playlist-resolver plugin bundles.
- `src/ax_devil/resources/` contains packaged assets; `resources/icons/` holds the bundled Lucide SVGs (ISC license
  alongside) drawn by `modules/chrome/icons.py`.
