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

Offline sources join the process-wide `FrameCachePool` through `data_sources/video_cache_memory.py`.
That module binds `GlobalSettings.video_cache_budget_changed` to the pool; settings owns the Auto/manual
preference, hardware detection and explanatory text. Auto uses 25% of Linux MemAvailable measured once at startup, with no fixed ceiling.
The pool divides the total allowance equally and shrinks caches before growing any share on source open/close or
setting changes. `FrameCache` serializes resizing with frame admission; decoder cleanup closes the cache and returns
its share. Pool code acquires cache locks, so cache code must release its lock before deregistering from the pool.
`CachedFrame` reserves the larger of source planes and eventual RGB24 pixels. Prefetch shares the allowance and
protects the current/nearer forward frames; oversized frames still deliver uncached. Diagnostic snapshots report
frame count, reserved bytes and each source's current share, not total process RAM.

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

The DataHub client uses encrypted HTTPS/WSS by default, with certificate verification intentionally disabled for
this development tool. This provides transport encryption without authenticating device identity and is not
suitable for production or hostile networks. HTTP/WS is explicitly selectable; there is no automatic transport
downgrade or certificate exception UI/persistence.
The client rejects all redirects before following them, including authenticated token requests and the WebSocket
handshake, so a redirect cannot reach another host or change protocols.
The session-token request starts without credentials, then responds to the advertised authentication scheme,
preferring Digest over Basic. A rejected Digest attempt does not fall back to Basic. Basic remains supported when
advertised alone, including over HTTP; that HTTP mode exposes credentials to anyone observing the connection.

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
selects the newest queued overlay at or before each frame within an inclusive, fixed 10 ms tolerance, independent of
FPS. Future samples wait; stale samples are discarded. Only matched samples enter live sticky persistence, whose
separate timeout controls reuse on subsequent frames. With sticky disabled, unmatched frames have no overlay.
Frame and overlay arrivals both release frames whose delay has elapsed; the engine is
intentionally event-driven and does not use a wall-clock timer for an idle stream.

`StreamMediaController` wires the selected frame and overlay signals into the sync adapter and forwards synchronized
results to `SceneFramePresenter`. Sharing an RTSP connection does not imply that frame and overlay arrivals are paired;
both inputs retain their capture timestamps for synchronization.

Timestamp matching policy also lives in synchronization. It answers only which overlay timestamp matches a video
timestamp: exact first, otherwise the latest previous overlay timestamp within the configured tolerance. Future overlay
timestamps are not selected before their video time is reached. File overlay providers consume that policy, but own
provider-specific lookup behavior such as sequence fallback and lookup metadata.

Offline viewing receives frame events through `OfflineSession`; each `OfflineLane` pulls overlay data through
`OverlayLookup.get_overlay_at_frame()`, and `SceneFramePresenter` assembles the display frame.
With sticky overlays enabled, `OverlayPersistencePolicy.select_from_source()` requests the latest sample
at or before the frame and applies expiry using its original timestamp. This selection is independent of
seek history and is shared with export; only live persistence keeps a last-sample cache. Frame-keyed
providers resolve annotation sequence IDs against the bound video `FrameTimeline`, so retained samples
use actual frame timestamps for expiry, including variable frame rates.

### Scene history

While indexing, file overlay providers feed every served sample to a `SceneHistoryCollector` and persist the resulting
`SceneHistoryRecords` with the index: each Scene event's label and involved entity ids, and each entity's runs of
consecutive samples with the classification types it had. `FileOverlaySource.scene_history()` places those records on
the bound video as a `SceneHistory`: events land on the first video frame at or after their sample, and objects span
exactly the frames whose looked-up sample contains them, using the provider's lookup matching plus the lane's sticky
selection (`OverlayPersistencePolicy.sample_selection()`). Lanes place the history again whenever the sticky settings
or the timestamp fallback policy change.

`MediaToolsPanel` is the viewer's Scene inspector sink and forwards each new per-frame update to the entity list, the
event log and the object history pane; each defers its work while hidden, including inside a collapsed side panel. With a history, the entity list can switch between the displayed frame and every
object in the file (highlighting those on the displayed frame and applying the decoder's type filters to the recorded
types), the **Events** tab lists every event (searchable by text and filterable by the event kinds it holds), and selecting an event or entity shows an `ObjectCard` per involved
object: a presence strip over the video with its event ticks, and a button that opens the object's data on the displayed
frame, following playback. Links, the strip and double-clicked events request frames through the panel, which offline
lanes route to `OfflineSession.jump_to_frame()`. Live viewers have no history: their event log appends the events of
newly shown overlays and keeps the newest 1000.

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
interpreter. `modules/plugin_installation/` owns the separate locked uv project, combining an editable app checkout,
exact installed app release, or the app's direct wheel/Git source with selected plugins, constrained to the dependency
versions installed with the app. Storage belongs to the checkout/base-environment location; an atomic `current` link
activates a prepared project after app/source identity and plugin validation. App, Python, installed-dependency, or
project-metadata changes invalidate that runtime; the next launch rebuilds it and falls back to the current base app
if that fails. Plugin management never modifies the base environment.
See [Creating and using plugins](../plugins.md) for commands, upgrades, storage, and recovery.

Built-in decoder plugin bundles:

| Plugin ID | File handlers | Payload handlers |
|-----------|---------------|------------------|
| `adf-v1` | `ADF_V1_FRAME`, `ADF_V1_CONSOLIDATED` | `ADF_V1_FRAME` |
| `adf-v1-beta` | `ADF_BETA_FRAME`, `ADF_BETA_CONSOLIDATED` | `ADF_BETA_FRAME` |
| `axis-cvat` | `CVAT` | none |
| `mot` | `MOT_FILE` | none |
| `uvg-vcm` | `UVG_VCM` | none |
| `axis-onvif-xml` | `ONVIF_XML` | `ONVIF_XML` |

The UVG-VCM provider parses the whole v1.0 JSON document into a derived frame cache, keeping normalized geometry
and using video sequence indices for alignment. See the [dataset contract](../datasets/uvg-vcm.md#decoder-behavior)
for supported annotations and ambiguous tracking IDs.

Built-in playlist resolver bundles:

| Plugin ID | Purpose |
|-----------|---------|
| `folder_pair` | Matches videos and overlays from separate folders by file name. |
| `mot_challenge` | Resolves MOT Challenge datasets into playlist content. |

## UI Structure

The main UI is a workspace shell: content browser on the left, drag-to-split viewer area in the center, and application actions in `MainWindow`.

Key owners:

- `MainWindow`: application shell, menus, shortcuts, dialogs, diagnostics windows, and `WorkspaceSession`.
- `WorkspaceSession`: public Workspace facade for composition, content mutation, startup loading, focused viewer lookup, and
  teardown.
- `ApplicationWindow`: static central layout shell.
- `WorkspaceController`: UI coordination and synchronization between content browser, state, split view, and viewers.
- `SplitView`: pane layout, drag/drop splitting, focused widget tracking, and removal/collapse behavior.
- `WorkspaceWidget`: common pane frame and lifecycle contract.
- `LiveVideoViewerWidget`: live Video Viewer workflow.
- `OfflineVideoViewerWidget`: offline and playlist Video Viewer workflow.
- `FrameDisplay`: reusable display shell.
- `FrameViewport`: interactive viewing area.
- `VideoFrameRenderer`: coalesces frame delivery and prepares frames and overlay drawings for the Quick surface.

New top-level windows should inherit from `ChromeWindow`; new dialogs should inherit from `BaseDialog`.

Application appearance is owned by `modules/chrome/theme.py`: shared light and dark color overrides feed QDarkTheme,
and theme setup completes Qt's application palette so custom painters receive matching surfaces, borders and selection
colors. Automatic OS changes refresh that palette after the theme engine finishes applying its stylesheet. Widget styles
use palette roles, rich-text inspection content inherits its surface's foreground, and status colors select readable
foregrounds for each appearance. Playback controls retain white icons and text on their dark video scrim.

## Data Flow Summary

Live pipeline:

```
RTSPSource.frameReady -> QtStreamSync.push_frame()
RTSPSource.overlayReady OR MQTTOverlaySource.overlayReady -> QtStreamSync.push_overlay()
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
- `IndexedFrameCache`: persisted decoded frame artifacts for providers that need derived caches.
  Stores binary pickle payloads with optional per-frame gzip compression (level 1) after a JSON header.
  Header offsets and lengths support direct frame reads without decoding other frames; payloads need no text encoding
  or line separators.
  Once opened, frame reads use the retained handle until close, even if the cache path is removed.
- Scene stores: `SourceIndexedSceneStore` for source-record indexes and `IndexedFrameSceneStore` for decoded frame
  artifacts. Both accept persisted data only when its complete artifact identity matches the source fingerprint, decoder
  name, explicit artifact version, Scene model version, and provider-owned decode options.
- `MetricsStore`: latest application-wide source observations, off while Collect Debug Metrics is unchecked.
- `RenderMetricsStore`: viewer lifetimes, atomic paint samples, and bounded recent timing history.
- `DebugWindow`: viewer-focused rendering diagnostics, separate source/cache details, and stack recording; see [Diagnostics](../../src/ax_devil/modules/diagnostics/README.md).
- `ExceptionHandler`: global exception hook with optional dialog.
