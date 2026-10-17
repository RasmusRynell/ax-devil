# Architecture Overview

The code is organized around concept-owned modules. Workspace content describes what can be opened; viewer workflows create runtime sources and presentation objects only when content is opened.

## Layers

```
CLI (`cli.py`)                 -> Click commands that build Workspace Items
Application (`app.py`)         -> logging, config, settings, plugins, Qt event loop
Application Shell              -> MainWindow, menus, app-wide dialogs including Settings
Workspace                      -> core: Workspace, items, content, intake (Qt-free); ui: store, browser rows, panes, viewers
Video Viewer                   -> live/offline workflows, media tools, Scene presentation, catalog viewer
Video Player                   -> reusable frame display, viewport, drawing preparation
Runtime Modules                -> Scene, filtering, data sources, synchronization, cache, plugins
Supporting Modules             -> diagnostics, shortcuts, chrome, background jobs
Settings                       -> config, settings state, preference values, logging, paths
Core                           -> small foundational data types and pure policies
```

Modules import downward. Settings imports nothing above it, so every module may log and read configuration. These
upward imports are deliberate, and none forms an import-time cycle:

- `scene.rendering` imports the drawing contract from `video_player.engine`; see the rendering README.
- Viewers are `ViewerWidget`s, and the Workspace viewer factory constructs them, so `video_viewer` and `workspace`
  depend on each other.
- Plugins speak the host's types: playlist resolvers return Workspace content and decoders return data-source
  factories.
- Diagnostics reads the modules it reports on: dashboard snapshots read the frame cache, the plugin window reads the
  plugin system, and render metrics name the video player's metrics type as a type-only import.
- `core.data_types` names the Scene type that `OverlayData` carries, as a type-only import.

For code placement, see [Module Map](module-map.md). For UI framework boundaries, see [UI Framework Structure](ui-framework.md).

## Workspace Content

Content (`ax_devil.modules.workspace.core.content`) is an immutable description of what the user added, never a running
source: seekable video, live video, and playlists of entries and lanes. Typed source specifications are the source of
truth for standard source details such as paths, handler types, stream settings and MQTT settings; content and lane
metadata is reserved for plugin-defined or workflow-specific information that no typed field holds.
`OverlaySourceKind` owns the display names of the lane source kinds.

Content declares what takes part in consideration (exclusions): playlist entries, visible playlist lanes and standalone
seekable overlay lanes. Live overlays do not, because live viewers cannot change their lane layout. Playlists are
offline workflows, so `PlaylistEntry` rejects live video lanes.

## Workspace Items And Intake

Everything that adds content, from the CLI to the Add dialogs and file drops, creates Workspace Items; the store
resolves them into Content. The item model, Content identity, lifecycle and the playlist resolver contract are in
[Workspace](workspace.md).

`WorkspaceIntake` validates decoder selections and constructs video Content for items; it also validates the device
host, camera head, and MQTT and WebSocket settings, so dialog, CLI and saved items share the same requirements.
`workspace/ui/plugin_intake.py` supplies the production intake and resolution context by adapting plugin definitions.
File decoders may declare `file_extensions`, and the Add Video dialog and file drops pick the handler when exactly one
matches. Live entry points convert persisted overlay strings into `LiveOverlayMode` at their boundaries.

## Opening Content

`WorkspaceViewerFactory` routes by concrete content type to the offline or live viewer widget. Offline viewers receive
the read-only `ConsiderationQuery` contract, while `WorkspaceStore` remains the mutable state owner.
`WorkspaceController` places the widget in `SplitView`, tracks which item its Content came from, and closes it when that
item is removed.

## Workspace State

`WorkspaceStore` (`workspace/ui/workspace_store.py`) owns the mutable workspace facts and their Qt signals: the current
and last saved Workspace (file format in [Workspace](workspace.md#file-format)), what each item resolved to
([resolution](workspace.md#resolution)), and consideration refs. `workspace_replaced` fires when another Workspace is
opened or created, and `state_changed` when the modified flag, name or path changes.

`build_browser_sections` (`workspace/ui/browser_rows.py`) projects the contents, the consideration query and the open
items into the rows every sidebar view renders (see [UI Framework](ui-framework.md#sidebar-views)). The views do not
decide playlist expansion, lane expansion, open-row identity or information payloads.

## Data Sources

The pipeline has three delivery capabilities: `FrameSource` emits `FrameData`, `OverlaySource` emits `OverlayData`, and
`OverlayLookup` returns `OverlayData` by `FrameIdentifier` with no playback lifecycle. Implementations and transport
rules are in the [data_sources README](../../src/ax_devil/modules/data_sources/README.md). File overlays do not produce
data or maintain playback position: `FileOverlaySource` owns its decoder-backed provider, serves synchronous lookups,
and closes the provider explicitly and idempotently. Push-based live overlay sources keep the `OverlaySource`
lifecycle.

Offline sources join the process-wide `FrameCachePool`; its rules are in the
[data_sources README](../../src/ax_devil/modules/data_sources/README.md#offline-frame-cache) and the user-facing setting
in [Settings](../settings.md#video-cache-memory).

Data-source timing and alignment report models live with the data sources that produce them. `core.data_types` is
reserved for shared frame identity and timestamped frame/overlay packets.

### Live source composition

`StreamMediaController` builds sources from `LiveVideoContent`: RTSP always supplies frames, an embedded RTSP overlay
shares that source, and MQTT or DataHub overlays add a second source. Source selection follows the content
specification; implementing `OverlaySource` does not mean embedded overlays are enabled. The controller keeps
frame/overlay inputs separate from its collection of owned sources, so it manages each transport once per lifecycle
operation even when one object supplies both inputs.

Each owned transport is one feed of a `LiveConnectionStatus` (`video_viewer/live_connection.py`): the RTSP source is
the video feed and a separate MQTT or DataHub source is the overlay feed. Sources report `sourceConnected`,
`sourceReconnecting(reason)` for retries they handle themselves, and `sourceError(reason)` for failures that need the
user's Retry, which reopens every transport from the same content. A video feed without frames while playing is
stalled and also needs Retry.

`data_sources/live/datahub_client.py` and `data_sources/live/mqtt_discovery.py` own the DataHub and MQTT protocol
clients shared by Workspace discovery and the runtime sources.

## Synchronization

Live viewing uses `StreamSync`, a pure Python engine wrapped by `QtStreamSync`. It buffers frames by arrival delay and
pairs each with the newest overlay at or before it within a fixed tolerance; it is event-driven, with no wall-clock
timer. The matching and persistence rules are in [Frame Identity](../domain/invariants.md#frame-identity).
`StreamMediaController` wires the frame and overlay signals into the sync adapter and forwards synchronized results to
`SceneFramePresenter`. Sharing an RTSP connection does not imply that frame and overlay arrivals are paired; both
inputs retain their capture timestamps. Timestamp matching policy also lives in synchronization; file overlay
providers consume it but own their own lookup behavior, such as sequence fallback and lookup metadata.

Opening an offline entry happens in two phases. `open_entry_media` opens the entry's video and overlay sources on a
worker thread (frame index, overlay parsing, the scene history and alignment the lanes show first) while the viewer
shows a loading indicator, then delivers `EntryMedia` on the GUI thread, and `OfflineSession.build` creates the
displays over it without touching files. Navigating while an entry opens abandons it and opens the newer one;
openings run one at a time, so rapid navigation never indexes many files at once.

Offline lanes pull overlay data through `OverlayLookup` and apply sticky selection through `OverlayPersistencePolicy`;
offline selection is independent of what was shown before, and only the live path keeps a last-sample cache. Live
viewers have no Scene history. Changing the timestamp fallback policy or sticky overlays places the history and
alignment report again on the `overlay-analysis` background pool from an immutable `OverlaySamples` and a copy of the
video's timeline; the lane keeps showing the previous ones until the newest request finishes.

## Scene Model And Rendering

`Scene` is the universal decoded world-model format: entities with time-sorted observations, events, and directed
relations, in normalized `[0,1]` image space (`ax_devil.modules.scene.model`). Payload-to-Scene decoder contracts live
under `ax_devil.modules.scene.decoding`; plugin discovery and handler registration under
`ax_devil.modules.plugin_system`.

Rendering turns a filtered `Scene` into prepared drawings through the active Scene Render Catalog, and the video
player binds the result to a Qt Quick surface used for both display and export. The boundary between the two is
[Draw System Architecture](draw-system.md).

## Plugin System

Plugins are discovered by `ApplicationPluginLoader` and stored in `RuntimePluginRegistry`. A decoder plugin
contributes file-to-Scene and/or payload-to-Scene decoders; a playlist resolver plugin resolves user-selected datasets
into `PlaylistContent`, and requires a settings widget. `ax-devil list-handlers` lists what is loaded.

Built-in plugins are imported from ax-devil's package. External plugin distributions are discovered only through the
`ax_devil.decoder_plugins` and `ax_devil.playlist_resolver_plugins` entry-point groups visible to the current Python
environment. Plugin API version, class contract, plugin IDs, and decoder handler collisions are validated before
registration. One invalid external entry point does not prevent other plugins or the application from loading.

`launcher.py` selects the prepared plugin interpreter before Qt imports; management commands stay in the base
interpreter. `modules/plugin_installation/` owns the separate locked uv project that holds installed plugins; its rules
are in [Plugin System invariants](../domain/invariants.md#plugin-system).
See [Installing and managing plugins](../plugins.md) for commands, upgrades, storage, and recovery, and the
[write-plugin skill](../../.agents/skills/write-plugin/SKILL.md) for writing plugins. The UVG-VCM and MOT decoders are
described with their datasets: [UVG-VCM](../datasets/uvg-vcm.md#decoder-behavior),
[MOT Challenge](../datasets/mot-challenge.md#decoder-behavior).

## UI Structure

The main UI is a workspace shell: an activity bar and sidebar on the left, a drag-to-split viewer area in the center,
and application actions in `MainWindow`. Its owners are in [UI Framework Structure](ui-framework.md).

Application appearance is owned by `modules/chrome/theme.py` and `tokens.py`; see the
[chrome README](../../src/ax_devil/modules/chrome/README.md).

## Infrastructure

Config, settings, shortcuts, cache, diagnostics and exception handling are mapped in [Module Map](module-map.md). The
scene stores (`SourceIndexedSceneStore`, `IndexedFrameSceneStore`) invalidate by the rule in
[Data Pipeline](../domain/invariants.md#data-pipeline).
