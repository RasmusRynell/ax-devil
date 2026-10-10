# Domain Invariants

Rules that code in more than one module must keep, and that no type, validation or test enforces on its own.
Read the sections for the area you change. How things fit together is in the architecture docs.

Not here: behavior the user sees (usage and settings docs), rules one module keeps to itself (that module's
README), Qt workarounds and measurements (a comment at the line, or the testing runbook). See the placement rule in
[AGENTS.md](../../AGENTS.md#where-a-fact-belongs).

## Content Model

- Workspace content (`SeekableVideoContent`, `LiveVideoContent`, `OverlayContent`, `EntryLane`, `PlaylistContent`)
  describes what the user added. It stores source specs and metadata, never instantiated sources or factories.
  Runtime sources are created when a viewer opens content: offline by `EntryOpening`, live by `StreamMediaController`.
- Each overlay source spec owns its `OverlaySourceKind`; overlay content and lanes derive the kind from the spec.
- Seekable video accepts file overlays. Live video accepts at most one RTSP, MQTT, or DataHub WebSocket overlay, chosen
  explicitly as `LiveOverlayMode`; config and CLI strings are converted at their boundaries.
- Every playlist lane references `SeekableVideoContent`. Playlists are offline workflows.
- `WorkspaceIntake` validates decoder selections and live connection settings for every video and live stream
  opened through a dialog, the CLI or a file drop, so those paths share one set of requirements. Playlists from a
  resolver plugin enter as built; the resolver owns their validity.

## Coordinates

- `BoundingBox`, `Polygon`, and `NormalizedPoint` use normalized `[0,1]` image space. Catalog lengths resolve against
  the current target, not source-video dimensions.
- Prepared drawings and styles are immutable values. Never mutate shared vertex bytes, Qt value objects or text
  layouts after publication.

## Display Rendering

- Video and overlay data are handed to the display as one synchronized pair; queued updates may coalesce to the
  latest pair. Offline delivery presents only the newest frame queued for the GUI thread; seeks and frame steps still
  present the requested frame.
- Offline playback follows the clock: a delivery worker that falls behind skips to the frame due now instead of
  slowing down; the video's final frame is always delivered.
- Scene and catalog interpretation stay on the GUI side of the renderer boundary; scene graph callbacks consume
  prepared data only. GPU resources are created and retired only in Qt's scene graph phase.
- Display and export use the same Quick renderer, without Scene semantics.

## Frame Identity

- Every frame has both `FrameIdentifier.sequence_id` and `FrameIdentifier.timestamp_monotime_us`. Offline timestamps
  are measured from the start of the video; live timestamps from the Unix epoch.
- Timestamp matching is a synchronization policy: exact timestamp first, then the latest previous overlay timestamp
  within the configured tolerance. Future overlay timestamps are never selected early.
- Timestamp-keyed files match exact timestamps, then configured past tolerance. Frame-keyed files (MOT/CVAT) match
  annotation sequence IDs and resolve sample age through the video's actual frame timeline, never synthetic keys or FPS.
- With sticky overlays enabled, offline playback and export select the latest sample at or before the requested time,
  regardless of navigation history. Age is measured from the original sample timestamp, never the last matched frame.
  The timeout includes its boundary; no timeout means unlimited retention. Matching tolerance distinguishes normal
  matches from retained samples, which are the ones drawn at the retained opacity; it does not limit sticky
  retention. With sticky disabled, unmatched frames have no overlay. Empty scene updates replace previous geometry;
  missing updates may retain it.
- Live synchronization consumes the newest queued sample at or before the frame within an inclusive, fixed 10 ms
  tolerance, independent of FPS. Stale samples are discarded before persistence; late older samples cannot replace
  newer state.
- File overlay packets retain the matched sample timestamp, and its sequence when the provider knows one; the
  requested frame stays in lookup metadata.
- Alignment diagnostics compare on the provider's actual basis: timestamps for timestamp-keyed overlays, frame numbers
  for sequence-keyed overlays.

## Scene Model

- `Scene` is the exchange format for decoded world-model data. All decoders produce it; filtering, inspection, and
  rendering consume it.
- Published Scenes are snapshots. Publish a replacement Scene when content changes instead of mutating a delivered one;
  unchanged identities may reuse filtered and prepared rendering data.
- `Entity.observations` stays time-sorted; decoders add through `Entity.add_observation()`.
- `Classification.type` is a dynamic string; `KnownClassificationType` is only a convenience for recognized values.
  Recipe routing is catalog policy: UI code never switches on classification strings.
- `EntityRelation` is a directed link between entity IDs. A relation with a missing endpoint is valid Scene data but is
  not rendered.
- `Scene.debug` and `Observation.debug` are decoder-owned, free-form, and picklable. Inspection shows
  `Observation.debug`; `Scene.debug` is kept for diagnostics only. Filtering, rendering, synchronization, and
  hit-testing never read either.
- Changing Scene model dataclass fields requires bumping `SCENE_MODEL_VERSION`: major when breaking, minor when
  additive. Decoder plugins declaring another major, or a newer minor, fail to load and are reported at startup.

## Workspace And Viewers

- Open, focused, pinned, and current on-screen viewer facts come from the hosted `WorkspaceWidget` instances; they are
  not copied into parallel state records.
- Video Viewer navigation reads consideration through the read-only `ConsiderationQuery` contract, not the mutable
  `WorkspaceManager`.
- Content removal closes every viewer widget that tracks the removed content. `SplitView` exclusively owns hosted
  widget removal and deletion; callers use its removal API.

## UI Lifecycle

- UI is a projection of runtime state, not the owner of it. Widgets receive state from the module, service, or
  controller that owns the concept, and widget construction never rebuilds shared runtime state as a side effect.
- Work that discovers files, reads disk, validates, compiles, builds indexes, opens sources, or creates shared caches
  belongs behind the owning runtime boundary, never in a widget constructor.
- Text sizes, spacing, radii and chrome heights come from `modules/chrome/tokens.py`, never literals, and anything
  sized from text or colored from the palette reruns through `chrome.appearance.follow_appearance`, so the theme and
  **Text size** apply live. Pixels inside rendered frames (catalogs, sheets, export burn-ins) follow the frame instead.
  The rest of the chrome rules are in the [chrome README](../../src/ax_devil/modules/chrome/README.md).
- Widgets that own runtime resources implement and call `cleanup()`.
- The GUI thread never waits for offline file work and never spins the event loop to wait. `EntryOpening` opens an
  entry's sources on a worker thread and delivers `EntryMedia` on the GUI thread; whoever holds the media owns every
  source in it until `release_media`. Worker threads never hold the last reference to objects they hand to the GUI
  thread.
- Queued callbacks from replaced or cleaned-up live sources cannot affect the live viewer. Sources own their workers
  and defer their own deletion until the worker finishes; the controller never inspects worker internals.
- Live sources report failures they retry themselves with `sourceReconnecting` and failures needing a manual retry
  with `sourceError`; the viewer shows both with their reason, never only in logs.
- Automatic cyclic GC is disabled after startup; `app.py` freezes the startup heap and collects on a GUI-thread timer
  so worker threads never finalize Qt objects held in reference cycles. Do not re-enable `gc`, call `gc.collect()` off
  the GUI thread, or change thresholds to improve a benchmark. Keep long-lived Scene history light for the collector.

## Synchronization

- `StreamSync` syncs live data by capture time, not arrival time, and is event-driven with no wall-clock timer, so an
  idle tail frame stays buffered until another input arrives. Sharing an RTSP connection does not pair frame and
  overlay arrivals; both inputs keep their capture timestamps.
- Offline lanes pull overlay data from an `OverlayLookup` by `FrameIdentifier`; file overlays have no playback
  lifecycle and their owning runtime closes them explicitly. Live overlays are push-based through `overlayReady`.

## Plugin System

- External plugins are installed distributions discovered only through the supported entry-point groups, and must
  declare the host's plugin API version. The base runtime never imports an external plugin merely because its source
  directory exists.
- Plugin IDs are unique within each plugin type; decoder `handler_type` strings are unique across loaded decoders for
  each capability.
- Plugin load failures must not stop the application. External load, validation and registration failures are
  recorded in `RuntimePluginRegistry`; a built-in import failure may only be logged.
- `ax-devil` selects the prepared plugin interpreter before importing application code; management stays in base.
  Plugin dependencies live in a separate locked uv project constrained to the app's exact dependency closure; plugin
  upgrades never upgrade the app, and installation activates a prepared project only after validation.
- App, Python, installed-dependency, or editable-project metadata changes trigger a refresh on the next launch; a
  failed refresh starts the base app without external plugins and is retried only by an explicit plugin change.

## Data Pipeline

- Pausing RTSP keeps its connection alive. Stopping is terminal; reopening creates a new source.
- Scene file provider artifacts invalidate unless their complete identity matches: source fingerprint, decoder name,
  explicit artifact version, Scene model version, and provider-owned decode options. Cache files (by default under
  `~/.ax_devil/caches/`) can always be rebuilt.
- Both storage modes persist Scene history records built from the sample lookup serves for each timestamp. Offline
  history places a timestamp-keyed event on the first video frame at or after its sample and a frame-keyed one on its
  sequence frame; an object is present on exactly the frames whose looked-up sample contains it, under the same
  matching and sticky selection as the lane's lookup.
- Whole-file entity filtering judges each distinct set of recorded classification types through the same
  classification policy as Scene entities; it is unavailable unless every filter option declares one, and history
  never fabricates Entity data.
- One process pool shares the Playback cache allowance across open offline sources. Cache pressure must not change
  frame identity or timing; a frame larger than the budget is delivered without caching.

## Export

- Export writes to an owned temporary sibling file and replaces the destination only after success. Source/output
  aliases are rejected; cancellation and failure cleanup never delete the destination.
- Exports preserve relative source frame timestamps and frame durations; unreadable frames fail the export. Export
  borrows a frozen filter snapshot and the catalog variant active when it starts, so later edits cannot alter an
  export in progress.

## Rendering

- Scene rendering lives under `ax_devil.modules.scene.rendering`; drawing preparation and submission live under
  `ax_devil.modules.video_player`, which never imports or interprets `Scene`.
- Overlay draw order is geometry, then paths, then text, then labels. Order across entities and within a layer is not
  preserved, so a catalog never relies on one shape covering another.
- Each recipe evaluates once per frame for all its routed entities or relations. A row that fails a demanded check
  emits none of its recipe's output, and a recipe whose evaluation raises emits nothing for that frame; other rows
  and recipes are unaffected.
- Built-in catalogs are read in place from the installed package and never copied, written or deleted. Every JSON file
  in the user catalog directory is a user catalog, and the app never writes catalog contents; they are edited outside
  it. Catalog writes (copies) validate before atomically replacing the destination.
- The default catalog is the only catalog choice remembered across restarts. Overlay visibility belongs to each view or
  lane, independently of filters and catalog authoring; hiding a feature keeps its inspection and hover data.
- A failed catalog load or reload keeps the last compiled catalog in use and reports the error; it never leaves a view
  without a catalog.

## Settings And Shortcuts

- Settings changes apply on OK. Settings that need a restart carry one shared marker, and saving one offers to restart
  after the exit-time saves. [Quick Setup](../settings.md#quick-setup) is the one exception.
- Saves validate every editable field, replace the configuration atomically, and only then emit runtime change
  signals; failed saves leave active viewers untouched. Startup storage locations stay active until restart.
- Runtime-mutable settings are consumed through `GlobalSettings` signals, not by polling `ConfigManager`. Settings
  live in one immutable `SettingsState` that setters replace whole.
- On/off overlay preferences are `OverlayPreference` members; adding a member adds it to the View menu, Settings and
  the saved config.
- All shortcuts are defined in `DEFAULT_SHORTCUTS`; persistence is override-only, so an empty `"shortcuts"` means all
  defaults apply.
