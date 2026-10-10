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
Supporting Modules             -> diagnostics, shortcuts, chrome
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

Workspace content lives in `ax_devil.modules.workspace.core.content`. Content objects are immutable descriptions of what the user added, not running sources.

| Type | Role |
|------|------|
| `SeekableVideoContent` | Offline video description with `FileVideoSourceSpec` and optional file overlays. |
| `LiveVideoContent` | Live video description with `LiveRTSPStreamSpec` and at most one RTSP, MQTT, or DataHub WebSocket overlay. |
| `OverlayContent` | Companion data with a label and a file, embedded RTSP, MQTT, or DataHub WebSocket source specification. |
| `EntryLane` | One visible lane with display name, video, optional overlay, source kind, metadata, and default consideration state. |
| `PlaylistEntry` | One playlist step containing one or more seekable lanes. |
| `PlaylistContent` | Ordered inspection sequence of playlist entries. |

`OverlaySourceKind` owns the display names and descriptions for file, RTSP, MQTT, DataHub WebSocket, and no-overlay lanes. `SeekableVideoContent.standalone_lanes()` and `LiveVideoContent.standalone_lanes()` expand standalone content into the lanes the sidebar and viewers should render.

Typed source specifications are the source of truth for standard source details such as paths, handler types, stream settings, and MQTT settings. Content and lane metadata is reserved for plugin-defined or workflow-specific information that is not already represented by a typed field.

Content also declares its supported consideration items. Playlist entries, visible playlist lanes, and standalone seekable overlay lanes participate in consideration. Live overlays do not expose consideration controls because live viewers do not support changing their lane layout.

`PlaylistEntry.__post_init__()` rejects live video lanes. Playlists are offline comparison/navigation workflows.

## Workspace Items And Intake

Bare `ax-devil` launch reopens the workspace kept at the last close; `ax-devil open` opens a saved one. Everything
that adds content — `ax-devil local`, `ax-devil live`, resolver commands, the Add dialogs, and desktop file drops —
creates Workspace Items
(`VideoItem`, `LiveStreamItem`, `PlaylistItem` in `workspace/core/items.py`); the store resolves them into Content. The
item model, Content identity, and the playlist resolver contract are described in [Workspace](workspace.md).

`WorkspaceIntake` validates decoder selections and constructs video Content for items. `workspace/ui/plugin_intake.py`
supplies the production intake, which adapts plugin decoder definitions into `WorkspaceDecoderOption` records, and the
production resolution context, which also looks up playlist resolver plugins. File decoders may declare
`file_extensions`; `WorkspaceIntake.file_decoder_options_for(path)` returns the decoders that may read an overlay file,
and the Add Video dialog and file drops pick the handler when exactly one matches. Live entry points convert persisted
overlay strings into `LiveOverlayMode` at their boundaries. Intake also validates the device host, camera head, and MQTT
and WebSocket connection settings so dialog, CLI, and saved items share the same content requirements.

## Opening Content

`WorkspaceViewerFactory` routes by concrete content type:

- `SeekableVideoContent` -> `OfflineVideoViewerWidget`
- `PlaylistContent` -> `OfflineVideoViewerWidget`
- `LiveVideoContent` -> `LiveVideoViewerWidget`

The factory returns the constructed widget and a status message. Offline viewers receive the read-only `ConsiderationQuery` contract, while `WorkspaceStore` remains the mutable state owner. `WorkspaceController` inserts the widget into `SplitView`, tracks which item its Content came from, wires lifecycle signals, and closes the widget when that item is removed.

## Workspace State

`WorkspaceStore` (`workspace/ui/workspace_store.py`) owns the mutable workspace facts and their Qt mutation signals:

- the current Workspace and the last saved one, with `open_workspace` and `save_workspace` reading and writing files
  (see [Workspace](workspace.md#file-format))
- what each item resolved to: its Content, the reason it could not resolve, or that it is still resolving in the
  background (see [Workspace](workspace.md#resolution))
- consideration refs (exclusions)

It signals item changes, `workspace_replaced` when another Workspace is opened, created, or restored, and
`state_changed` when the modified flag, name, or path changes.

`build_browser_rows` (`workspace/ui/browser_rows.py`) projects the contents, the consideration query, and the open
items into `WorkspaceBrowserRow` values. Open-row identity is supplied from the current items reported by hosted viewer widgets. The package split is described in [Workspace](workspace.md). `ContentBrowserWidget` renders explicit browser rows and emits activation, removal, and consideration intents. It does not decide playlist expansion, lane expansion, open-row identity, or information payloads.

## Data Sources

The data pipeline has three delivery capabilities:

| Interface | Emits | Lifecycle |
|-----------|-------|-----------|
| `FrameSource` | `FrameData` | `play()`, `pause()`, `stop()`, `wait()` |
| `OverlaySource` | `OverlayData` | `play()`, `pause()`, `stop()`, `wait()` |
| `OverlayLookup` | Returns `OverlayData` by `FrameIdentifier` | No playback lifecycle; the owning `FileOverlaySource` is closed explicitly |

Current source implementations:

| Source | Base | Transport/storage | Mode |
|--------|------|-------------------|------|
| `FileFrameSource` | `SeekableFrameSource` | PyAV / FFmpeg | Seekable offline video |
| `FileOverlaySource` | `QObject`, implements `OverlayLookup` | Decoder-backed Scene providers | Pull-based offline overlay |
| `RTSPSource` | `FrameSource`, `OverlaySource` | `ax-devil-rtsp` | Live video with optional embedded overlay decoding |
| `MQTTOverlaySource` | `OverlaySource` | `ax-devil-mqtt` | Live overlay |
| `WebSocketOverlaySource` | `OverlaySource` | `aiohttp` / Axis DataHub | Live overlay |

`Worker(QThread)` standardizes transport-source execution with `play -> pause -> stop` and `QMutex`/`QWaitCondition`
pause handling. `FileFrameSource` instead owns one `FileFrameDelivery` worker for paced playback, explicit frame
requests, PyAV serialization, lazy prefetch, callback delivery, and shutdown. `Seekable` adds `jump_to()`,
`get_total_frames()`, `get_current_frame()`, and `step_delta()`.

Offline sources join the process-wide `FrameCachePool` through `data_sources/video_cache_memory.py`, which binds
`GlobalSettings.video_cache_budget_changed` to the pool; its rules are in the
[data_sources README](../../src/ax_devil/modules/data_sources/README.md#offline-frame-cache) and the user-facing
setting in [Settings](../settings.md#video-cache-memory).

File overlays do not produce data or maintain playback position. `FileOverlaySource` owns its decoder-backed provider,
serves synchronous `OverlayLookup` requests, and closes that provider explicitly and idempotently. Push-based live
overlay sources retain the `OverlaySource` production lifecycle.

Data-source timing and alignment report models live with the data sources that produce them. `core.data_types` is reserved for shared frame identity and timestamped frame/overlay packets.

### Live source composition

`StreamMediaController` builds sources from `LiveVideoContent`:

| Overlay selection | Frame input | Overlay input | Owned sources |
|-------------------|-------------|---------------|---------------|
| None | RTSP | None | One RTSP source |
| Embedded RTSP | RTSP | Same RTSP source | One RTSP source |
| MQTT | RTSP | MQTT | RTSP and MQTT sources |
| DataHub WebSocket | RTSP | DataHub WebSocket | RTSP and WebSocket sources |

`RTSPSource` shares connection management, buffering, and frame conversion across both RTSP modes.
Its optional `RTSPOverlayDecoder` groups the payload decoder, handler identity, and filter factory.
Without that definition, it requests no scene metadata stream. Source selection follows the content specification;
implementing `OverlaySource` does not mean embedded overlays are enabled.

The controller keeps frame/overlay inputs separate from its collection of owned sources, so it manages each transport
once per lifecycle operation even when one object supplies both inputs. Transport rules are in the
[data_sources README](../../src/ax_devil/modules/data_sources/README.md).

Each owned transport is one feed of a `LiveConnectionStatus` (`video_viewer/live_connection.py`): the RTSP source is
the video feed and a separate MQTT or DataHub source is the overlay feed. Sources report `sourceConnected`,
`sourceReconnecting(reason)` for retries they handle themselves, and `sourceError(reason)` for failures that need the
user's Retry, which reopens every transport from the same content. A video feed without frames while playing is
stalled and also needs Retry.

`data_sources/live/datahub_client.py` and `data_sources/live/mqtt_discovery.py` own the DataHub and MQTT protocol
clients shared by Workspace discovery (`workspace/ui/add_content/analytics_discovery.py`) and the runtime sources.

## Synchronization

Live viewing uses `StreamSync`, a pure Python engine wrapped by `QtStreamSync`. It buffers frames by arrival delay and
pairs each with the newest overlay at or before it within a fixed tolerance; it is event-driven, with no wall-clock
timer. The exact matching and persistence rules are in [Frame Identity](../domain/invariants.md#frame-identity).

`StreamMediaController` wires the selected frame and overlay signals into the sync adapter and forwards synchronized
results to `SceneFramePresenter`. Sharing an RTSP connection does not imply that frame and overlay arrivals are paired;
both inputs retain their capture timestamps for synchronization.

Timestamp matching policy also lives in synchronization; file overlay providers consume it but own their own lookup
behavior, such as sequence fallback and lookup metadata.

Opening an offline entry happens in two phases. `EntryOpening` opens the entry's video and overlay sources on a
worker thread (frame index, overlay parsing, and the scene history and alignment the lanes show first) while the
viewer shows a loading indicator, then delivers `EntryMedia` on the GUI thread. `OfflineSession.build` creates the
displays over that media without touching files. Navigating while an entry opens abandons it and opens the newer one;
openings run one at a time, so rapid navigation never indexes many files at once.

Offline viewing receives frame events through `OfflineSession`; secondary video sources are pooled and fan each
completed frame out to the lanes that reference them. Each `OfflineLane` pulls overlay data through
`OverlayLookup.get_overlay_at_frame()`, applies sticky selection through `OverlayPersistencePolicy`, and
`SceneFramePresenter` assembles the display frame. Offline selection (`select_from_source`) is independent of
what was shown before; only the live path (`select_overlay`) keeps a last-sample cache.

While indexing, file overlay providers record each Scene event and each entity's runs of samples as
`SceneHistoryRecords`; `FileOverlaySource.scene_history()` places them on the bound video under the lane's matching
rules, and `MediaToolsPanel` shows them in the entity list, event log and object history. Live viewers have no
history.

## Scene Model And Rendering

`Scene` is the universal decoded world-model format:

- `Scene`: `time_slice`, `entities`, `events`, directed `EntityRelation` values, and free-form `debug`
- `Entity`: tracked object with time-sorted observations, images, end reason, and optional movement state
- `Observation`: geometry, classifications, timestamp/frame number, confidence, velocity, optional coordinates, and
  free-form `debug`
- `Classification`: dynamic string type, score, and attributes
- Geometry: `BoundingBox`, `Polygon`, and `NormalizedPoint` in normalized `[0,1]` image space
- Operations: `Delete`, `Rename`, `Merge`, and `Split`
- Relations: dynamic relation type, source entity, and target entity

Payload-to-Scene decoder contracts and helper utilities live under `ax_devil.modules.scene.decoding`. Plugin discovery and handler registration live under `ax_devil.modules.plugin_system`.

Rendering turns a filtered `Scene` into prepared drawings through the active Scene Render Catalog, and the video
player binds the result to a Qt Quick surface used for both display and export. The boundary between the two is
[Draw System Architecture](draw-system.md).

## Plugin System

Plugins are discovered by `ApplicationPluginLoader` and stored in `RuntimePluginRegistry`.

| Plugin type | Base class | Purpose |
|-------------|------------|---------|
| Decoder | `DecoderPlugin` | Contributes file-to-Scene decoders and/or payload-to-Scene decoders. |
| Playlist resolver | `PlaylistResolverPlugin` | Resolves user-selected datasets into `PlaylistContent`; requires a settings widget and may provide a CLI command. |

Built-in plugins are imported from ax-devil's package. External plugin distributions are discovered only through the
`ax_devil.decoder_plugins` and `ax_devil.playlist_resolver_plugins` entry-point groups visible to the current Python
environment. Plugin API version, class contract, plugin IDs, and decoder handler collisions are validated before
registration. One invalid external entry point does not prevent other plugins or the application from loading.

`launcher.py` selects the prepared plugin interpreter before Qt imports; management commands stay in the base
interpreter. `modules/plugin_installation/` owns the separate locked uv project that holds installed plugins; its rules
are in [Plugin System invariants](../domain/invariants.md#plugin-system).
See [Installing and managing plugins](../plugins.md) for commands, upgrades, storage, and recovery, and the
[write-plugin skill](../../.agents/skills/write-plugin/SKILL.md) for writing plugins.

Built-in decoder plugin bundles:

| Plugin ID | File handlers | Payload handlers |
|-----------|---------------|------------------|
| `adf-v1` | `ADF_V1_FRAME`, `ADF_V1_CONSOLIDATED` | `ADF_V1_FRAME` |
| `adf-v1-beta` | `ADF_BETA_FRAME`, `ADF_BETA_CONSOLIDATED` | `ADF_BETA_FRAME` |
| `axis-cvat` | `CVAT` | none |
| `mot` | `MOT_FILE` | none |
| `uvg-vcm` | `UVG_VCM` | none |
| `axis-onvif-xml` | `ONVIF_XML` | `ONVIF_XML` |

The UVG-VCM and MOT decoders' behavior is described with their datasets:
[UVG-VCM](../datasets/uvg-vcm.md#decoder-behavior), [MOT Challenge](../datasets/mot-challenge.md#decoder-behavior).

Built-in playlist resolver bundles:

| Plugin ID | Purpose |
|-----------|---------|
| `folder_pair` | Matches videos and overlays from separate folders by file name. |
| `mot_challenge` | Resolves MOT Challenge datasets into playlist content. |

## UI Structure

The main UI is a workspace shell: an activity bar and a sidebar (start panel or content browser) on the left, drag-to-split viewer area in the center, and application actions in `MainWindow`.

[UI Framework Structure](ui-framework.md) describes its owners: `MainWindow`, `WorkspaceSession`, `SplitView`,
`ViewerWidget` and the display stack.

Application appearance is owned by `modules/chrome/theme.py`: shared light and dark color overrides feed QDarkTheme,
theme setup completes Qt's application palette so custom painters receive matching surfaces, borders and selection
colors, and the body text size becomes the application font. Both change live; sizes derive from
`modules/chrome/tokens.py`, and widgets that style themselves rerun that code through
`modules/chrome/appearance.py`.

## Data Flow Summary

Live pipeline:

```
RTSPSource.frameReady -> QtStreamSync.push_frame()
RTSPSource / MQTTOverlaySource / WebSocketOverlaySource .overlayReady -> QtStreamSync.push_overlay()
QtStreamSync.syncReady -> StreamMediaController._on_sync_result()
  -> SceneFramePresenter.prepare_frame()
  -> FrameDisplay.display_frame()
  -> LiveStatusPanel.display_frame_status()
  -> queue Scene inspection update
DataSource.sourceConnected / sourceReconnecting / sourceError, video frames, stall timer
  -> StreamMediaController connection status -> LiveStatusPanel.show_connection_status()
```

Offline pipeline:

```
FileFrameSource -> FileFrameDelivery -> PyAV frame decoder/cache
  -> FileFrameSource.frameReady -> _FrameDeliveryRelay.deliver() (newest queued frame wins)
  -> OfflineSession._on_frame_ready()
  -> OfflineLane.present_frame()
  -> OverlayLookup.get_overlay_at_frame(frame_id)
  -> SceneFramePresenter.prepare_frame()
  -> FrameDisplay.display_frame()
  -> queue Scene inspection update
```

## Infrastructure

- `ConfigManager`: config file I/O, version validation, default merging, unsupported-key warnings with raw config preservation, runtime path/env expansion, and storage directory creation.
- `GlobalSettings`: reactive runtime settings singleton backed by config on load/save.
- `ShortcutManager`: default shortcut registration, `QAction` installation, override-only persistence, conflict detection, and rebinding.
- `CacheManager`: hash-based cache path generation and cache clearing.
- Scene stores: `SourceIndexedSceneStore` keeps a cache index of record offsets into the source file;
  `IndexedFrameSceneStore` persists decoded frame artifacts. Their invalidation rule is in [Data Pipeline](../domain/invariants.md#data-pipeline).
- Diagnostics: metrics stores, the debug window and stack recording; see the
  [diagnostics README](../../src/ax_devil/modules/diagnostics/README.md).
- `ExceptionHandler`: global exception hook with optional dialog.
