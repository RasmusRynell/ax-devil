# Domain Invariants

Rules that must remain true across the system. Violating these causes subtle bugs, data corruption, or crashes.
Read the sections for the area you change. Explanations of how things work live in the architecture docs.

## Content Model

- Workspace-owned content descriptions live under `ax_devil.modules.workspace`.
- `SeekableVideoContent`, `LiveVideoContent`, `OverlayContent`, `EntryLane`, and `PlaylistContent` are descriptions, not live runtime objects.
- Content stores source specs and metadata, not instantiated sources or source factories.
- Offline runtime source construction is owned by `video_viewer/offline_entry_media.py`; `OfflineSession` builds
  displays over the opened `EntryMedia`.
- Live runtime source construction is owned by `StreamMediaController`.
- Viewer routing depends on the concrete content type: `SeekableVideoContent`, `LiveVideoContent`, or `PlaylistContent`.
- Each overlay source spec owns its `OverlaySourceKind`; overlay content and lanes derive the kind from that spec.
- Embedded RTSP analytics, MQTT analytics, DataHub WebSocket analytics, and file overlays all use `OverlayContent`; a plain lane uses no overlay.
- `LiveOverlayMode` is the typed selection used by live-stream startup and intake; config and CLI strings are converted at
  their boundaries.
- Seekable video accepts file overlays. Live video accepts at most one RTSP, MQTT, or DataHub WebSocket overlay.
- `WorkspaceIntake` validates positive camera heads, decoder selections, and live transport connection settings before live content
  enters the Workspace.
- `PlaylistEntry` is lane-based, and every playlist lane must reference `SeekableVideoContent`.
- Playlists contain at least one entry, and every playlist entry contains at least one lane.
- `EntryLane` carries lane label, video, optional overlay, metadata, and default consideration state; source kind is derived.
- Workspace content declares its supported consideration items and their defaults. Playlist entries, visible playlist
  lanes, and standalone seekable overlay lanes are supported; live overlay consideration is not.
- Startup Request types and request dispatch live under `ax_devil.modules.workspace.startup_request`; content validation and
  construction resolve through the session-owned `WorkspaceIntake`.

## Coordinates

- `BoundingBox`, `Polygon`, and `NormalizedPoint` use normalized `[0,1]` image space.
- `NormalizedPoint` rejects out-of-range coordinates unless `allow_outside=True`.
- Prepared drawings and styles are immutable values. Never mutate shared vertex bytes, Qt value objects or text layouts after publication.
- Catalog instructions resolve normalized coordinates directly into final backend data; no intermediate primitive list is produced.

## Display Rendering

- Video and overlay data are handed to the display as one synchronized pair; queued updates may coalesce to the latest pair.
- Offline frame delivery presents only the newest frame queued for the GUI thread. Superseded frames never reach
  overlay lookup, presentation or Scene inspection; seeks and frame steps still present the requested frame.
- Offline playback follows the clock. When the delivery worker falls behind, `FileFrameDelivery` skips to the frame
  due now instead of slowing down, and still delivers the last frame. Skipped frames are never converted to RGB or
  delivered.
- Scene/catalog interpretation stays on the GUI side of the renderer boundary; scene graph callbacks consume prepared data.
- Display and export use the same Quick renderer, without Scene semantics. Export reads back native-size pixels on the GUI thread.
- GPU resources are created and retired only in Qt's scene graph phase. Viewer cleanup releases retained frame references.
- Quick diagnostic durations measure GUI preparation, not GPU execution or screen presentation.

## Frame Identity

- Every frame has both `FrameIdentifier.sequence_id` and `FrameIdentifier.timestamp_monotime_us`.
- Offline timestamps are measured from the start of the video.
- Live timestamps are measured from the Unix epoch.
- Timestamp matching is a synchronization policy: exact timestamp first, then the latest previous overlay timestamp
  within the configured tolerance. Future overlay timestamps are not selected early.
- Timestamp-keyed files match exact timestamps, then configured past tolerance. Frame-keyed files (MOT/CVAT) match
  annotation sequence IDs and resolve sample age through the video's actual frame timeline, never synthetic keys or FPS.
  With sticky disabled, missing annotation frames stay empty; with sticky enabled, the latest earlier sequence is eligible.
- With sticky overlays enabled, offline playback and export select the latest sample at or before the requested time,
  regardless of navigation history. Age is measured from the original sample timestamp, never the last matched frame.
  The timeout includes its boundary; no timeout means unlimited past retention. Future samples are never eligible.
- Matching tolerance distinguishes normal matches from retained samples (including retained opacity); it does not limit
  sticky retention. Empty scene updates replace previous geometry; missing updates may retain it.
- Live synchronization consumes the newest queued sample at or before the frame within an inclusive, fixed 10 ms
  tolerance, independent of FPS. Future samples wait; stale samples are discarded before persistence. With sticky
  disabled, unmatched frames have no overlay. With sticky enabled, only matched samples seed retention, and the
  separate timeout applies to original sample age; late older samples cannot replace newer state.
- File overlay packets retain the matched sample timestamp and sequence when available; the requested frame remains in
  lookup metadata. Both frame identifier fields remain populated.
- Overlay alignment diagnostics must use the provider's actual alignment basis: timestamp-keyed overlays compare timestamps, and sequence-keyed overlays compare frame numbers.

## Offline Video Cache Memory

- One process pool shares the Playback cache allowance equally across open offline sources, including paused sources.
  Lanes sharing one source count once. Opening, closing or changing the preference resizes existing caches immediately.
- Auto uses 25% of available RAM measured at startup, with no fixed ceiling. Manual mode specifies a total allowance.
  Neither mode preallocates memory.
- Pool rebalancing shrinks caches before growing shares; the sum of shares never exceeds the total allowance after rebalancing.
- Close releases the source's cache and share even if its QObject remains alive. Cache locks must be released before
  entering pool lifecycle methods; the pool may acquire multiple cache locks during rebalancing.
- Cached frames reserve the larger of source-plane bytes (including padding) and full RGB24 pixels. LRU admission
  keeps the sum within the byte budget, including prefetched frames. Conversion does not change the reservation.
- Prefetch may evict older history, but must not evict the current or nearer forward frames to retain a farther frame.
- A frame larger than the budget is delivered without caching. Cache pressure must not change frame identity or timing.
- This is a cache reservation limit, not an RSS limit. Decoder/intermediate seek frames, conversion temporaries,
  display images, overlay caches and allocator-retained pages are outside it.

## Scene Model

- `Scene` is the exchange format for decoded world-model data.
- Published overlay samples are snapshots. Publish a replacement Scene when content changes instead of mutating
  a previously delivered Scene; unchanged identities may reuse filtered and prepared rendering data.
- All decoders produce `Scene`; filtering, inspection, and rendering consume `Scene`.
- `Entity.observations` must stay time-sorted. Use `Entity.add_observation()`.
- `EntityId` rejects empty strings.
- `Entity.motion_state` is `None` when motion was not provided; explicit `MotionState.Unknown` is distinct and visible.
  Moving, stationary, and unknown states are displayed when motion indicators are enabled; absent motion is omitted.
- `Classification.type` is a dynamic string. `KnownClassificationType` is only a convenience enum for recognized values.
- `EntityRelation` stores a directed semantic link between two entity IDs; relations with a missing endpoint are valid
  Scene data but are not rendered.
- Operations (`Delete`, `Rename`, `Merge`, `Split`) mirror Axis Scene Metadata semantics.
- `Scene.debug` and `Observation.debug` are decoder-owned, free-form, and picklable. `Scene.debug` is retained for
  decoder/provider diagnostics without a UI display; `Observation.debug` appears in object details and hover cards.
  Filtering, rendering, synchronization, and hit-testing never read them.

## Workspace UI State

- `WorkspaceManager` owns content, consideration refs, browser-row projection, and mutation signals.
- Consideration refs identify current Workspace content, playlist entries, or lanes; orphan and out-of-range refs are ignored.
- Video Viewer navigation consumes the read-only `ConsiderationQuery` contract, not the mutable `WorkspaceManager`.
- `WorkspaceManager` emits one notification per successful mutation; batch additions are not expanded into
  per-item notifications.
- Open, focused, pinned, and current on-screen viewer facts come from the hosted `WorkspaceWidget` instances; they are not
  copied into parallel state records.
- `WorkspaceController` owns Qt side effects for viewer construction, insertion, removal, focus, signal wiring, cleanup,
  and widget-to-content dependency tracking.
- `ContentBrowserWidget` renders explicit `WorkspaceBrowserRow` values and emits user intents.
- Content removal must close every viewer widget that tracks the removed content.

## UI Lifecycle

- UI is a projection of runtime state, not the owner of it.
- Widgets should receive state from the module, service, or controller that owns the concept.
- Work that discovers files, reads disk, validates data, compiles data, builds indexes, opens sources, or creates
  shared caches belongs behind that owning runtime boundary.
- Runtime state should be created once, shared by all consumers, and refreshed through explicit lifecycle events or user
  actions.
- Widget construction must not rebuild shared runtime state as a side effect.
- Top-level windows should inherit from `ChromeWindow`.
- Dialogs should inherit from `BaseDialog`.
- Application chrome and inspection panels must remain readable in light and dark mode, including on an open widget
  after an OS theme change. Use the shared palette and semantic status colors; rich text inherits its surface's foreground.
  White playback controls belong on the dark video scrim rather than on a theme-dependent panel surface.
- Text sizes, margins, gaps, corner radii and repeated chrome heights come from `modules/chrome/tokens.py`, not
  literals. Text roles and heights derive from the body text size set by the **Text size** preference, which applies
  live like the theme. Widgets that only inherit the application font and palette follow on their own, and so do
  stylesheets that use `palette(...)` and fixed tokens, and icons from `chrome/icons.py` drawn without a fixed color.
  Application icons come from that bundled set, not Qt standard pixmaps or text glyphs. Code that sizes something from the text or colors something
  from the palette (`TextRole.apply`, `Height`, fixed-color icons, status colors) runs through
  `chrome.appearance.follow_appearance`, which reruns it on palette and text-size changes; sizes are never computed
  at import. The video surface and its letterbox stay near-black (`chrome.theme.VIDEO_CANVAS`) in both themes, so
  they do not follow the palette and no Python event filter sees every frame they repaint. Rich text carries weight
  and family only and takes its size from the label or document showing it.
  Pixels inside rendered frames (render catalogs, catalog-viewer footage and sheets, export burn-ins) follow the
  frame, not these tokens.
- The theme gives standard single-line inputs and push buttons a shared `Height.CONTROL` minimum and vertical
  padding. Natural size hints may grow controls for taller fonts or icons; wrapped content and expanded list rows
  keep their content-driven height.
- Vertical content layouts keep their rows at the top with a trailing `addStretch`, not `setAlignment(AlignTop)`:
  an aligned layout is sized by its size hint, ignores wrapped-text height, and overlaps rows when space is short.
- Shared geometry rules live in `modules/chrome/window_geometry.py`. Initial windows fit the available screen, and
  secondary `ChromeWindow` windows without saved state open at 85% of their top-level parent width and height,
  centered and bounded to the screen, including windows that remember their size. With saved state, parented windows
  restore their size and maximized state and center over their parent, bounded to the screen.
- The main window restores its last size and maximized state from `window-state.ini` in the configured storage base
  directory. Without saved state, it opens at 75% of the desktop-selected screen width and height on first activation, after desktop placement.
  Saved size and state are prepared before showing, with size bounded to the available screen. Restored normal sizes
  are checked again on first activation, after the desktop selects the monitor.
  Main-window placement belongs to the desktop: do not select a screen, move it, or persist screen coordinates.
  This UI state is separate from app config.
- `BaseDialog` opens at its content's full preferred size, bounded to the available screen and a comfortable maximum
  of 120 average character widths by 48 line spacings. This limit scales with the dialog font and only applies on opening;
  the user can enlarge the window. Wrapped content is measured at the bounded width. It prepares that geometry
  before the window is mapped. After that only the user resizes it: the same path works on stacking and tiling window
  managers, which may ignore or reposition application-driven resizes. A dialog whose own controls change its
  content's size, such as Quick Setup changing the text size, opens at the size its largest content needs
  (`opening_size_hint`) and keeps its content at natural size inside, instead of resizing later. It scrolls content
  independently of its action buttons when the screen or the user makes it smaller. Re-showing the same dialog preserves the user's size and position.
- A dialog containing its own scrolling view or pages opts out of the outer content scroll area. Views get layout
  stretch, and the content area fills the dialog so those views take the spare height. Settings pages and shortcut lists
  use the shared `ContentScrollArea` to report their natural content size.
  Information trees use Qt's content size adjustment and their own scrolling. Avoid nesting scrolling containers.
- Dialogs inherit custom chrome through both window and dialog parents. Temporary modal dialogs use
  `with SomeDialog(...) as dialog:` so results remain readable inside the scope and the dialog is deleted afterwards.
  `BaseDialog.done()` calls the idempotent `cleanup()` hook for every outcome; exiting a temporary scope also cleans up
  if showing the dialog failed. Nonblocking, single-use dialogs are deleted on close.
- Content that changes with a selection (live overlay transports, playlist resolvers) lives in `QStackedWidget` pages
  built up front, so the content's preferred size already covers every choice and selecting one never needs a resize.
  Use `align_label_columns` when stacked forms sit below other form rows. Do not add per-dialog fixed or minimum
  opening sizes. Give shorter pages their choice's description (owned by the choice, such as `LiveOverlayMode`
  or a plugin definition) instead of leaving the reserved space blank.
- Add Live Stream validates inline: OK stays disabled while the form is incomplete, and a message says what to fix.
- Input forms use `modules/chrome/form_layout.py`: compact top-aligned rows, growing fields, and wrapping on narrow screens.
  Use layout stretch and widget size policies to give extra space to views and editors; reserve fixed sizes for controls
  whose physical dimensions are intentional, such as icons and resize handles.
- Shared minimum sizes are 480 x 320 for windows and 320 x 200 for dialogs (Qt logical pixels).
- Widgets that own runtime resources must implement and call `cleanup()`.
- `SplitView` exclusively owns hosted `WorkspaceWidget` removal and deletion; callers must use its removal API.
- Live source workers process queued packets; `StreamMediaController` coordinates their lifecycle from the UI thread.
  Respect existing `play -> pause -> stop` lifecycle hooks. Queued callbacks from replaced or cleaned-up sources
  cannot affect the live viewer.
- Live sources report failures they retry themselves with `sourceReconnecting` and failures needing a manual retry
  with `sourceError`; the live viewer shows both with their reason, never only in logs.
- Pull-based file overlay lookup has no playback lifecycle; its owning runtime closes it explicitly.
- The GUI thread never waits for offline file work and never spins the event loop to wait. `EntryOpening` opens an
  entry's sources on a worker thread and delivers `EntryMedia` on the GUI thread; `OfflineSession.build` only creates
  widgets over it. Whoever holds the media owns every source in it, including those opened before a failure, until
  `release_media` closes them on a worker thread and deletes them on the GUI thread. Openings run one at a time; an
  abandoned opening opens nothing more and releases what it opened instead of delivering it. A viewer being destroyed
  closes its installed session's sources before returning and abandons any opening still in progress. Worker threads
  never hold the last reference to objects they hand to the GUI thread.
- `Worker.run_loop()` returns `False` to exit the thread loop and `True` to continue.
- Qt sync adapters must break callback references in `cleanup()` so the core does not retain Qt objects.
- Automatic cyclic GC is disabled after startup; `app.py` freezes the startup heap and collects on a GUI-thread timer
  so worker threads never finalize Qt objects held in reference cycles. Do not re-enable `gc` or call `gc.collect()`
  off the GUI thread.

## Synchronization

- `StreamSync` syncs live data by capture time, not arrival time. An exact capture-time match does not require a following frame.
- Live synchronization is event-driven: each frame or overlay arrival emits queued frames whose configured delay has
  elapsed, with an overlay or with `None`. An idle tail frame intentionally remains buffered until another input arrives;
  the bounded queue prevents unbounded growth.
- `SyncResult.frame` is always present. `SyncResult.overlay` may be `None`.
- Shared timestamp matching policy lives under `ax_devil.modules.synchronization`; file provider lookup metadata lives with file data providers.
- Offline viewer lanes pull overlay data from an `OverlayLookup` by `FrameIdentifier`.

## Plugin System

- External plugins are installed distributions discovered only through supported Python entry-point groups.
- Plugin classes use classmethods for discovery metadata and definitions.
- External plugin classes must explicitly declare the host's plugin API version.
- Plugin IDs are unique within each plugin type.
- Decoder `handler_type` strings are unique across loaded decoder plugins for each decoder capability.
- Built-in plugin roots are always searched.
- The base runtime never imports a plugin merely because its source directory exists.
- `ax-devil` / `uv run ax-devil` selects the prepared plugin interpreter before importing application code; management stays in base.
- Plugin dependencies belong to a separate locked uv project, constrained to the exact installed versions of the app's
  dependency closure. Startup installs plugin packages only to rebuild an outdated runtime for the same selection.
- Managed runtimes use the editable app checkout, exact base app release, or the same direct wheel/Git source; plugin
  upgrades never upgrade the app.
- App, Python, installed-dependency, or editable-project metadata changes trigger a refresh on the next launch; stale
  runtimes never launch, and a failed refresh starts the base app without external plugins. A failed refresh is not
  retried automatically for the same fingerprint; any explicit plugin change is the retry.
- Plugin selections belong to the checkout/base-environment location and survive app upgrades at that location.
- Installation activates a prepared project only after app/source identity and plugin validation; host metadata and the base environment stay intact.
- The installer runs the base app's bundled uv binary; validator and managed-app subprocesses use isolated Python startup.
  Inherited uv source overrides cannot change selected sources.
- Plugin load failures must not stop the application. Failures that reach validation or registration are recorded in `RuntimePluginRegistry`; import failures may only be logged.

## Data Pipeline

- `FrameSource` emits `FrameData`; `OverlaySource` emits `OverlayData`.
- `FileOverlaySource` is pull-based through `get_overlay_at_frame()`.
- `MQTTOverlaySource` and `WebSocketOverlaySource` are push-based through `overlayReady`.
- `RTSPSource` always provides video and enables embedded overlay decoding only when configured.
- Live content selects the overlay input explicitly: none, embedded RTSP, separate MQTT, or separate DataHub WebSocket.
- DataHub uses encrypted HTTPS/WSS by default, with certificate verification intentionally disabled for this
  development tool. HTTP/WS remains explicitly selectable; there is no certificate exception state or automatic
  downgrade. Token requests and WebSocket handshakes reject redirects.
- The live controller calls each owned source once per lifecycle operation, including when RTSP supplies both frames
  and overlays. Sources own their workers and defer their own deletion until the worker finishes; the controller does
  not inspect worker internals. Repeated controller cleanup is a no-op.
- DataHub sample reception waits until data arrives or shutdown cancels it. Idle silence is not a transport error;
  connection and request deadlines remain bounded separately from streaming.
- Pausing RTSP keeps its connection alive. Stopping is terminal; reopening creates a new source.
- RTSP connects on its worker thread: `play()` never waits for the camera, and `stop()` cancels a pending connect.
- Scene file provider artifacts invalidate unless their complete artifact identity matches: source fingerprint, decoder
  name, explicit artifact version, Scene model version, and provider-owned decode options.
- Changing Scene model dataclass fields requires bumping `SCENE_MODEL_VERSION`: major when breaking, minor when
  additive. Decoder plugins declaring another major, or a newer minor, fail to load and are reported at startup.
- `StorageMode.SOURCE_INDEX` stores offsets into the original source file and decodes records on demand.
- `StorageMode.DERIVED_CACHE` stores decoded frame artifacts for whole-file or aggregated providers.
- Both storage modes persist Scene history records built from the sample that lookup serves for each timestamp:
  every event's label and involved entity ids, and every entity's runs of consecutive samples. Artifacts without
  history records are rebuilt.
- Offline history places events on the first video frame at or after their sample, because lookup never shows a
  sample before its timestamp; frame-keyed samples belong to their sequence frame. Events outside the video are not
  listed. An object is present on exactly the frames whose looked-up sample contains it, under the same matching and
  sticky selection as the lane's lookup, and the history is placed again when either changes. Classification types
  come only from samples that lookup serves.
- Whole-file entity filtering applies the id search to each object and judges each distinct set of recorded
  classification types once through the same explicit classification policy used for Scene entities. An empty set
  represents an unclassified object. Entity-only decoder predicates remain valid for Scene filtering, but whole-file
  scope is unavailable unless every option declares a classification policy; history never fabricates Entity data.
- Scene history stays light for the garbage collector: catalogs keep parsed history records, not their JSON form, and
  object spans are plain tuples of frame indices. Every long-lived tracked object lengthens the full collections that
  pause the shared GUI thread.
- Live event logs record an overlay's events once, on the first frame that shows it; sticky reuse does not repeat them.
- Tools that follow playback do no work while they cannot be seen: a collapsed side panel hides its content, and
  hidden lists, tabs and cards keep only the latest frame and catch up when shown. Live event logs still record
  events while hidden, since those cannot be recovered. `MediaToolsPanel` drops an update whose frame and Scene
  repeat the previous one.
- Scene file providers retain only the two most recently used decoded scenes, keyed by resolved source timestamp.
  Consecutive playback and immediate revisits reuse them; older scenes reload from indexed storage. Large decoded
  histories cause expensive garbage-collection pauses across viewers on the shared GUI thread.
- Cache files live under `~/.ax_devil/caches/` and can be rebuilt. An opened frame cache keeps reading through its
  retained file handle until closed, even if the cache file is removed.

## Export

- `ExportJob` owns the synchronous GUI-thread workflow and its rendering/output resources; it borrows sources and
  frozen presentation inputs. The dialog owns options and progress, and forwards cancellation to the job.
- Exports preserve relative source frame timestamps and available frame durations; unreadable frames fail the export.
- Export writes to an owned temporary sibling file and replaces the destination only after successful completion.
- Source/output aliases are rejected; cancellation and failure cleanup never delete the destination.
- Multi-lane export tiles the selected lanes in the viewer's lane grid and follows the first selected lane's frame
  index and timing, as playback does; a lane past its last frame holds that frame.

## Rendering

- Scene rendering lives under `ax_devil.modules.scene.rendering`.
- Presenters retain at most one prepared overlay sample and release it on missing data, replacement, and cleanup.
- `CachedSceneOverlay` owns filtered-scene caching, prepared-drawing caching, hover target indexing, and drawing preparation metrics. Filtering is timed at the actual build; cached retrieval does not masquerade as generation work.
- Rendering diagnostics distinguish submitted frames from completed CPU paints. Paint samples are atomic and bounded; total paint excludes screen presentation. Viewer diagnostic state is removed on cleanup, not on an idle timeout.
- New-frame intervals exclude repaints but include pauses and seeks. Paint cost distributions separate new frames from repaints. Source observations have independent monotonic timestamps and explicit viewer ownership; display labels are not source identities.
- Freezing diagnostics freezes the snapshot, not playback or capture; export preserves the frozen snapshot and selected paint.
- Spike inspection compares preceding paints of the same kind, excludes the event itself and future paints, and preserves missing measurements. Historical source context is retained per paint with explicit capture/field times; current source state must not substitute for it.
- Scene Render Catalog resolves one draw recipe per entity after finding the latest observation and primary
  classification. Catalog-owned fallback and classification recipes are part of the active Scene Render Catalog
  identity. Every catalog keeps both the `classified` and `unclassified` fallback recipes.
- Recipe routing is catalog policy. UI code never switches on classification strings.
- Hover hit indexing and drawing preparation use the same filtered Scene and latest-observation policy.
- Relation recipes resolve both endpoints from the filtered Scene and render only when both entities have observations.
- Each recipe evaluates once per frame for all its routed entities or relations. A row that fails a demanded check emits
  none of its recipe's output; other rows are unaffected.
- Overlay draw order is geometry, then paths, then text, then labels. Order across entities and within a layer is not preserved.
- `SceneRenderCatalogManager` owns runtime catalog discovery and compiled catalog reuse. `SceneRenderCatalogSelection`
  owns one consumer's active catalog path, compiled active catalog, visibility choices, status, and reload policy. Selectors and lane
  widgets must not read, validate, or compile catalog files during construction.
- Catalog listing is metadata discovery. Full catalog validation happens only when initializing, selecting,
  reloading, or creating a catalog. Visibility changes compile from retained validated definitions.
- Catalog previews and CLI checks use a source document and compiled catalog from one file read. The manager caches
  that revision together, so cached compilation never pairs with a fresh read of a different document.
- Built-in catalogs are read in place from the installed package and never copied, written or deleted. Every JSON
  file in the user catalog directory is a user catalog, whatever its name, and the app never overwrites one on startup
  or listing. The default catalog is the user's choice of starting catalog, and it is the only catalog choice remembered
  across restarts. Individual view and lane selections are not remembered.
- User overlay visibility belongs to each view/lane, independently of entity filters and catalog authoring. Hiding
  a feature retains its inspection and hover data. New views permit all features; catalog changes preserve choices.
- Visibility specialization occurs on settings changes, never per frame. Generated programs contain only selected
  output and do not evaluate inputs exclusive to omitted components. Hidden components still pass full validation.
- Failed reloads retain a usable validated definition for subsequent visibility changes. Visibility changes invalidate
  prepared drawing without refiltering the Scene or rebuilding hover data. Paused displays refresh immediately.
- Export captures the selected executable variant, so later visibility choices do not change an ongoing export.
- Catalog file loads validate structure before activation; failed loads retain the previous compiled catalog.
  The app does not write catalog contents; they are edited outside it.
- Value limits are data on the value types (and on template parameters that declare them). Validation, generated row
  checks and the generated language reference all read the same limits; only `RULES` holds checks that limits cannot express.
  Calculated values resolve by dependencies and evaluate lazily once per invocation. Rendering identity ignores
  mapping order and descriptive metadata, but preserves ordered steps and the compiler semantic revision.
- A selection records every failed load of its active path, including the validation done by **Apply to all** and
  **Use as default**, and clears it only after a successful compile. While a failure is recorded, its status shows the
  error and its selector disables **Apply to all** and **Use as default**.
- Catalog `enabled`, `if`, and `coalesce` short-circuit; unused arithmetic is not executed by constant folding.
- Parameters and calculated values have separate scopes. Template input types and nullable reference guards are checked
  before activation. Runtime recipe errors discard that recipe's entire output and preserve unaffected recipes.
- Lengths resolve against the target dimensions. Strokes of width zero are absent; fonts have no hidden size
  offset. Generic Qt painting retains subpixel geometry and remains independent of Scene semantics.
- Catalog writes validate before atomically replacing the destination.
- The drawing target, preparation and submission data live under `ax_devil.modules.video_player`, which never
  imports or interprets `Scene`.
- Fonts, shaped text, colors and geometry have bounded caches; prepared-drawing cache identity includes the catalog
  rendering identity and exact viewport, DPI/DPR and backend settings.

## Settings And Shortcuts

- Settings changes apply on OK or Apply; settings that need a restart carry one shared marker. The separate
  shortcut editor applies changes on its own OK, independently of the Settings dialog's Apply or Cancel.
  [Quick Setup](../settings.md#quick-setup) is the exception: its choices apply as they are clicked and are saved
  however it closes, because the running app is its preview.
- `ConfigManager` owns config file I/O, version validation, default merging, unsupported-key warnings, raw
  document preservation, runtime path/env expansion, and storage directory creation.
- Settings validates all editable connection and storage fields before applying any change. Saves replace the
  configuration atomically before emitting runtime change signals; failed saves leave active viewers untouched. Startup storage locations stay
  active until restart, even after new locations have been saved.
- `GlobalSettings` is a reactive `QObject` singleton between `ConfigManager` and UI consumers. Its sole runtime
  value is a complete immutable `SettingsState`; snapshots and UI edits use typed values, with disk parsing and
  serialization confined to load/save. Setters replace fields and retain the existing change signals.
- Runtime-mutable settings should be consumed through `GlobalSettings` signals, not by polling `ConfigManager`.
- On/off overlay preferences are `OverlayPreference` members, on by default and read through
  `GlobalSettings.is_overlay_enabled`; adding a member adds it to the View menu, Settings and the saved config.
- All keyboard shortcuts are defined in `DEFAULT_SHORTCUTS`.
- `ShortcutManager` installs once on `MainWindow`; late registration after install is rejected.
- Shortcut persistence is override-only. Empty `"shortcuts"` means all defaults apply.
