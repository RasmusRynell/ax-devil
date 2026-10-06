# Architecture Overview

The code is organized around concept-owned modules. Workspace content describes what can be opened; viewer workflows create runtime sources and presentation objects only when content is opened.

## Layers

```
CLI (`cli.py`)                 -> Click commands and startup requests
Application (`app.py`)         -> logging, config, settings, plugins, Qt event loop
Application Shell              -> MainWindow, menus, app-wide dialogs
Workspace                      -> content, intake, state, browser rows, split panes, viewer hosting
Video Viewer                   -> live/offline workflows, media tools, Scene presentation
Video Player                   -> reusable frame display, viewport, drawing preparation
Runtime Modules                -> data sources, synchronization, cache, playback, plugins
Supporting Modules             -> diagnostics, settings, shortcuts, chrome
Core                           -> small foundational data types
```

For code placement, see [Module Map](module-map.md). For UI framework boundaries, see [UI Framework Structure](ui-framework.md).

## Workspace Content

Workspace content lives in `ax_devil.modules.workspace.content`. Content objects are immutable descriptions of what the user added, not running sources.

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

## Startup Requests And Intake

Bare `ax-devil` launch opens an empty workspace without a startup request.

Startup request types live in `ax_devil.modules.workspace.startup_request`:

- `VideoFileStartup` for `ax-devil local --video ...`, and for videos opened from the Add Video dialog, desktop file
  drops, and the welcome screen's recent list
- `LiveStreamStartup` for `ax-devil live ...`
- `ResolvedPlaylistStartup` for resolver-provided playlists

`WorkspaceIntake` validates decoder selections and constructs Workspace content. Startup requests own the dispatch from each
request shape into that intake boundary. `WorkspaceSession` keeps one intake instance for startup loading. Intake adapts plugin
decoder definitions into `WorkspaceDecoderOption` records before UI or CLI startup paths consume them. File decoders may
declare `file_extensions`; `WorkspaceIntake.file_decoder_options_for(path)` returns the decoders that may read an overlay
file, and the Add Video dialog and file drops pick the handler when exactly one matches.
Live startup paths convert persisted overlay strings into `LiveOverlayMode` at their boundaries. Intake also validates
camera-head and MQTT connection settings so dialog, CLI, and programmatic startup share the same content requirements.

## Opening Content

`WorkspaceViewerFactory` routes by concrete content type:

- `SeekableVideoContent` -> `OfflineVideoViewerWidget`
- `PlaylistContent` -> `OfflineVideoViewerWidget`
- `LiveVideoContent` -> `LiveVideoViewerWidget`

The factory returns the constructed widget, its content dependencies, and a status message. Offline viewers receive the read-only `ConsiderationQuery` contract, while `WorkspaceManager` remains the mutable state owner. `WorkspaceController` inserts the widget into `SplitView`, tracks its content dependencies, wires lifecycle signals, and handles removal side effects.

## Workspace State

`WorkspaceManager` owns the mutable workspace facts and their Qt mutation signals:

- content list
- consideration refs
- derived `WorkspaceBrowserRow` values

Open-row identity is supplied when browser rows are projected from the current items reported by hosted viewer widgets. `ContentBrowserWidget` renders explicit browser rows and emits activation, removal, and consideration intents. It does not decide playlist expansion, lane expansion, open-row identity, or information payloads.

## Data Sources

The data pipeline has three delivery capabilities:

| Interface | Emits | Lifecycle |
|-----------|-------|-----------|
| `FrameSource` | `FrameData` | `play()`, `pause()`, `stop()`, `wait()` |
| `OverlaySource` | `OverlayData` | `play()`, `pause()`, `stop()`, `wait()` |
| `OverlayLookup` | Returns `OverlayData` by `FrameIdentifier` | Borrowers perform lookups; the concrete owner closes resources |

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
`GlobalSettings.video_cache_budget_changed` to the pool. The pool splits one allowance equally across open sources;
see [Offline Video Cache Memory](../domain/invariants.md#offline-video-cache-memory) for its rules and
[Settings](../settings.md#video-cache-memory) for the user-facing setting.

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
Without that definition, it registers no application-data callback. Source selection follows the content specification;
implementing `OverlaySource` does not mean embedded overlays are enabled.

The DataHub client's transport rules are in [Data Pipeline invariants](../domain/invariants.md#data-pipeline). Its session-token request starts without
credentials, then answers the advertised scheme, preferring Digest over Basic; a rejected Digest attempt does not fall
back to Basic. Basic over HTTP exposes credentials to anyone observing the connection.

The controller keeps frame/overlay inputs separate from its collection of owned sources. This lets it manage each
transport once per lifecycle operation and connect each source's errors once, even when one object supplies both inputs.
Live sources register their Qt workers with `DataSource`, which owns worker disposal and retains sources only while
requested deletion is waiting for a running worker. The controller uses source lifecycle methods without knowing
worker attributes. DataHub streaming awaits samples directly and uses task cancellation for shutdown; only connection
setup and complete protocol requests have deadlines.
See [Data Pipeline invariants](../domain/invariants.md#data-pipeline) for the lifecycle requirements.

### Live connection status

Each owned live transport is one feed of a `LiveConnectionStatus` (`video_viewer/live_connection.py`): the RTSP source
is the video feed, and a separate MQTT or DataHub source is the overlay feed. Embedded RTSP overlays share the video
feed. Sources report their connection through `DataSource` signals:

| Signal | Meaning | Feed state |
|--------|---------|------------|
| `sourceConnected` | The transport reached its peer | Live |
| `sourceReconnecting(reason)` | An attempt failed and the source retries on its own (MQTT) | Reconnecting (n) |
| `sourceError(reason)` | The source failed without an automatic retry | Failed |

A video frame also marks the video feed live, and a live video feed with no frames for `VIDEO_STALL_TIMEOUT_S` while
playing is stalled. Failed and stalled feeds need a manual retry: `StreamMediaController.retry()` releases every
transport and opens new ones from the same content. The controller publishes each status change to
`LiveVideoViewerWidget`, which shows the video state, every problem feed's reason, and a Retry button in
`LiveStatusPanel`, and the video state as the empty-pane text. State labels, descriptions, and colors live on
`LiveConnectionState`.

`data_sources/live/datahub_client.py` owns DataHub authentication, protocol messages, and topic discovery. Both the Workspace
discovery adapter and the Qt worker in `websocket_overlay_source.py` use this client. `data_sources/live/mqtt_discovery.py`
owns the MQTT source query shared by Workspace discovery and runtime source validation.

MQTT data-source and DataHub topic discovery share `AnalyticsChoiceLoader` in `workspace/add_content/analytics_discovery.py`.
Each instance owns its connection identity,
loading state, and stale-result rejection. `AnalyticsChoice` displays that state and preserves the selected choice;
the live-stream dialog supplies the transport-specific fetch function and defaults.

## Synchronization

Live viewing uses `StreamSync`, a pure Python engine wrapped by `QtStreamSync`. It buffers frames by arrival delay and
pairs each with the newest overlay at or before it within a fixed tolerance; it is event-driven, with no wall-clock
timer. The exact matching and persistence rules are in [Frame Identity](../domain/invariants.md#frame-identity).

`StreamMediaController` wires the selected frame and overlay signals into the sync adapter and forwards synchronized
results to `SceneFramePresenter`. Sharing an RTSP connection does not imply that frame and overlay arrivals are paired;
both inputs retain their capture timestamps for synchronization.

Timestamp matching policy also lives in synchronization; file overlay providers consume it but own their own lookup
behavior, such as sequence fallback and lookup metadata.

Offline viewing receives frame events through `OfflineSession`; each `OfflineLane` pulls overlay data through
`OverlayLookup.get_overlay_at_frame()`, and `SceneFramePresenter` assembles the display frame.
With sticky overlays enabled, `OverlayPersistencePolicy.select_from_source()` requests the latest sample
at or before the frame and applies expiry using its original timestamp. This selection is independent of
seek history and is shared with export; only live persistence keeps a last-sample cache. Frame-keyed
providers resolve annotation sequence IDs against the bound video `FrameTimeline`, so retained samples
use actual frame timestamps for expiry, including variable frame rates.

### Scene history

While indexing, file overlay providers record each Scene event and each entity's runs of samples as
`SceneHistoryRecords`, stored with the index. `FileOverlaySource.scene_history()` places them on the bound video, using
the lane's lookup matching and sticky selection, and lanes place them again when either changes. The placement rules
are in [Data Pipeline invariants](../domain/invariants.md#data-pipeline).

`MediaToolsPanel` is the viewer's Scene inspector sink. It forwards each per-frame update to the entity list, the event
log and the object history pane, which defer work while hidden. With a history, the entity list can show every object
in the file, the **Events** tab lists every event, and selecting one shows an `ObjectCard` per involved object with a
presence strip; frame links route to `OfflineSession.jump_to_frame()`. Live viewers have no history: their event log
appends the events of newly shown overlays and keeps the newest 1000.

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

Rendering turns a filtered `Scene` into prepared drawings through the active Scene Render Catalog: each
catalog-owned recipe evaluates once per frame for all entities or relations routed to it, and the video player binds
the result to a Qt Quick surface used for both display and export. `CachedSceneOverlay` owns the filtered Scene,
prepared-drawing cache, hover index and preparation metrics. The pipeline, built-in recipes, catalog language, backend
and open work are documented in [Draw System Architecture](draw-system.md).

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

The UVG-VCM decoder's behavior is described with the [dataset](../datasets/uvg-vcm.md#decoder-behavior).

Built-in playlist resolver bundles:

| Plugin ID | Purpose |
|-----------|---------|
| `folder_pair` | Matches videos and overlays from separate folders by file name. |
| `mot_challenge` | Resolves MOT Challenge datasets into playlist content. |

## UI Structure

The main UI is a workspace shell: content browser on the left, drag-to-split viewer area in the center, and application actions in `MainWindow`.

[UI Framework Structure](ui-framework.md) describes its owners: `MainWindow`, `WorkspaceSession`, `SplitView`,
`WorkspaceWidget` and the display stack.

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
- `IndexedFrameCache`: persisted decoded frame artifacts for providers that need derived caches, readable one frame at
  a time.
- Scene stores: `SourceIndexedSceneStore` for source-record indexes and `IndexedFrameSceneStore` for decoded frame
  artifacts. Both accept persisted data only when its complete artifact identity matches the source fingerprint, decoder
  name, explicit artifact version, Scene model version, and provider-owned decode options.
- `MetricsStore`: latest application-wide source observations, off while Collect Debug Metrics is unchecked.
- `RenderMetricsStore`: viewer lifetimes, atomic paint samples, and bounded recent timing history.
- `DebugWindow`: viewer-focused rendering diagnostics, separate source/cache details, and stack recording; see [Diagnostics](../../src/ax_devil/modules/diagnostics/README.md).
- `ExceptionHandler`: global exception hook with optional dialog.
