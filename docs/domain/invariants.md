# Domain Invariants

Rules that code in more than one module must keep, and that no type, validation or test enforces on its own. Each
holds whenever its feature exists. Read the sections for the area you change. Terms are defined in
[CONTEXT.md](../../CONTEXT.md); how things fit together is in the [architecture overview](../architecture/overview.md).

Not here: user-visible behavior, rules one module keeps to itself, workarounds and measurements. See the placement
rule in [AGENTS.md](../../AGENTS.md#where-a-fact-belongs). While a rule has no test guarding it, it is a test gap;
the module that implements it adds one.

## Content Model

- Workspace content (Seekable Video, Live Video, Overlay and Playlist Content, Entry Lanes) describes what a Workspace
  Item resolves to. It stores source specs and metadata, never opened sources or factories. Runtime sources are
  created when a viewer opens content.
- Workspace Items are the truth and Content is derived: everything that adds content creates items, and Content comes
  only from resolving an item. The Workspace, the store and the file format never branch on item kind.
- Content ids derive from the owning item's id and never change between resolutions, and Content records its item's
  id. Content built outside resolution has empty ids and is never top-level Content.
- Live Stream Items store connection values as entered; a `$VARIABLE` reference is expanded only during resolution,
  under the host binding rule of [13](../rebuild/13-security-packaging-ops.md#threat-model). A camera property
  (protocol, ports) is stored on the item and editable when adding it; changed defaults only prefill new items. An
  environment preference (connection timeout) is never stored; it is read when the stream connects.
- An item's label is what the user named it, or empty. An empty label shows a default name derived from the recipe
  when displayed; derived names such as an expanded host are never stored or saved.
- An item that fails to resolve stays in the Workspace with its resolution error recorded. An item the file format
  cannot read always fails to resolve and is saved back unchanged.
- Each overlay source spec owns its overlay source kind; overlay content and lanes derive the kind from the spec.
- Seekable video accepts file overlays. Live video accepts at most one RTSP, MQTT or DataHub WebSocket overlay, chosen
  explicitly as a typed mode; config and CLI strings are converted at their boundaries.
- Every playlist lane references Seekable Video Content. Playlists are offline workflows.
- One intake validates decoder selections and live connection settings for every video and live stream opened through
  a dialog, the CLI or a file drop. Playlists from a resolver enter as built; the resolver owns their validity.
- Layers 1–3 never depend on the UI or on how the UI reaches them; the architecture check enforces it.

## Coordinates

- Bounding boxes, polygons and points use normalized `[0,1]` image space. Catalog lengths resolve against the current
  target, not source-video dimensions.
- Prepared Drawings and styles are immutable values, never changed after publication.

## Display Rendering

- Video and overlay data are handed to the display as one synchronized pair; queued updates may coalesce to the
  latest pair. Offline delivery presents only the newest frame queued for the UI; seeks and frame steps still present
  the requested frame.
- Offline playback follows the clock: delivery that falls behind skips to the frame due now instead of slowing down;
  the video's final frame is always delivered.
- Scene and catalog interpretation happen before the renderer boundary; renderer callbacks consume prepared immutable
  data only. GPU resources are created and released only in the renderer's resource phase.
- Display and export share rendering semantics: the same instructions, order, colors and geometry, without Scene
  knowledge in the renderer. Pixels differ only by documented renderer tolerances.

## Frame Identity

- Every frame has a sequence id and a timestamp: signed 64-bit integer microseconds on the media clock, a Scene
  datetime's time since the Unix epoch. Live frames carry device capture time; an offline video's first frame is 0
  and its frames take their times from container presentation timestamps, never from FPS, so timestamp-keyed files for
  a recording count from the epoch and variable frame rates work. Units are converted in one place.
- Timestamp Matching: exact timestamp first, then the latest previous overlay timestamp within the configured
  tolerance. Future overlay timestamps are never selected early.
- Frame-keyed files (MOT/CVAT) match annotation sequence ids and resolve sample age through the video's actual frame
  timeline, never synthetic keys or FPS.
- With sticky overlays enabled, offline playback and export select the latest sample at or before the requested time,
  regardless of navigation history. Age is measured from the original sample timestamp, never the last matched frame.
  The timeout includes its boundary; no timeout means unlimited retention. Matching tolerance separates normal matches
  from retained samples, which are drawn at the retained opacity; it does not limit retention. With sticky disabled,
  unmatched frames have no overlay. Empty scene updates replace previous geometry; missing updates may retain it.
- Live synchronization consumes the newest queued sample at or before the frame within an inclusive, fixed 10 ms
  tolerance, independent of FPS. Stale samples are discarded before persistence; late older samples cannot replace
  newer state.
- File overlay packets keep the matched sample timestamp, and its sequence when the provider knows one; the requested
  frame stays in lookup metadata.
- Alignment diagnostics compare on the provider's actual basis: timestamps for timestamp-keyed overlays, frame numbers
  for sequence-keyed ones.

## Scene Model

- Scene is the exchange format for decoded world-model data: all decoders produce it; filtering, inspection and
  rendering consume it. It has one versioned serialization schema.
- Published Scenes are snapshots. Publish a replacement when content changes instead of mutating a delivered one;
  unchanged identities may reuse filtered and prepared rendering data.
- An entity's observations stay time-sorted.
- A classification type is a dynamic string; known values are only a convenience. Recipe routing is catalog policy:
  UI code never switches on classification strings.
- Score and confidence are separate: a decoder sets what its source provides and never derives one from the other or
  a default; a missing value stays missing and the sample is kept. Both are any finite number shown plain; a bar for a
  value in 0..1 is a display hint, not a range requirement. The primary classification ranks scored before unscored,
  then by score, then by confidence.
- A relation is a directed link between entity ids. A relation with a missing endpoint is valid Scene data but is not
  rendered.
- An observation's source data holds what the source supplied verbatim; its debug data holds what the decoder
  computed. Both hold only schema value types (null, bool, number, string, list, map); inspection shows and queries
  them under Debug, each field with its origin. Scene-level debug data is decoder-owned frame diagnostics, never
  displayed and without origin. Filtering, rendering, synchronization and hit-testing never read any of them.
- Changing the Scene contract bumps its version: major when breaking, including stricter value requirements, minor
  when additive. Scene datetimes are timezone-aware. A decoder declaring another major, or a newer minor, fails to
  load; the host normalizes accepted older-minor output before caching.

## Workspace And Viewers

- Open, focused, pinned, on-screen and live-status facts about viewers come from the hosted viewers; they are not
  copied into parallel state records.
- Viewer navigation reads Hidden state through a read-only query, never the mutable store.
- One store owns the current and last saved Workspace, each item's resolution, Hidden refs and mutations, and sends
  one change notification per successful mutation; "modified" is current ≠ saved. One projection turns the resolved
  Content and the open items into sidebar rows. The notification also reports a changed modified flag, name or path;
  a failed open changes nothing.
- Hidden refs identify current Workspace content, playlist entries or lanes; orphan and out-of-range refs are ignored.
- Removing Content removes its whole item and closes every viewer showing it. Opening a Workspace file closes every
  viewer, even when item ids are kept. The split host alone removes and destroys hosted viewers.
- Every item is listed: an item that failed to resolve is one unavailable row that shows its reason and never opens;
  an item still resolving is one pending row that opens nothing.
- Items resolve off the UI thread; the store commits a result only while its item and Workspace are still current.
- Only adding items opens a viewer. Opening or creating a Workspace lists its items and opens and connects nothing.
- Nothing prompts on launch, open, New or quit: a Workspace with unsaved edits has one recovery copy, never its own
  file, that exists exactly while the edits do and is deleted when they are saved or discarded.

## Sidebar

- The window is always in a Workspace; the activity bar has a Workspace zone and an app zone.
- A Workspace-zone view is the whole Workspace or one part of it: it lists the Workspace's own items and offers ways
  to add to them, and keeps no data of its own beyond what the Workspace file saves.
- An app-zone view is about the application and never shows Workspace content.
- Bookmarks and review marks apply to local videos and playlists, never to live streams.
- A sidebar view adds nothing to the viewer area; opening content from a view goes through the Workspace's one
  open-content path.

## UI Lifecycle

- UI is a projection of runtime state, not its owner. Components receive state from the module or service that owns
  the concept, and building a component never rebuilds shared runtime state as a side effect.
- Work that discovers files, reads disk, validates, compiles, builds indexes, opens sources or creates shared caches
  belongs behind the owning runtime boundary, never in UI construction.
- Text sizes, spacing, radii, heights and colors come from the design tokens, never literals, and a theme change
  applies live. Zoom is the browser's; there is no app text-size setting. Pixels inside rendered frames
  (catalogs, sheets, export burn-ins) follow the frame instead.
- The video surface and its letterbox stay near-black (one canvas token) in both themes, with white playback controls
  on that scrim, not on a theme-dependent panel.
- Components that own runtime resources have an explicit cleanup contract, and their owner calls it.
- The UI never blocks on file, network, decode, compile or index work, and never waits by nesting its event loop.
  Finite work runs as cancellable tasks owned by a scope that closes when its owner cleans up; a cancelled task
  delivers nothing. Work that changes something outside the app, or streams, keeps its own runner and finishes
  regardless of listeners.
- There is one cancellation contract; cancellation propagates through every layer a task calls, and generic error
  handling cannot swallow it.
- Opened media has one owner at a time and is handed over explicitly; resources are destroyed on the executor they
  require, and an unclaimed result's resources are released.
- Callbacks from replaced or cleaned-up live sources cannot affect the viewer. Sources own their workers; quit waits
  for retiring sources within one deadline, so transport cleanup such as removing temporary device configuration
  completes.
- Live sources report failures they retry themselves (reconnecting) and failures needing a manual retry (error), each
  with a reason; the viewer shows both, never only the logs.
- Memory management never pauses frame delivery.

## Synchronization

- Live sync pairs data by capture time, not arrival time, and is event-driven with no wall-clock timer, so an idle
  tail frame stays buffered until another input arrives. Sharing an RTSP connection does not pair frame and overlay
  arrivals; both inputs keep their capture timestamps.
- Every live queue is bounded with an overflow policy its owner defines, and every drop is counted in the timing
  diagnostics. Encoded video is discarded codec-aware and resyncs at the next decodable keyframe. Payload decode is
  lossless: its queue overflowing is a lane error ("overlay decoder behind") that holds until the queue drains, resets
  the decoder and leaves a gap marker in Events. Drop-oldest applies only after decode. A burst never stalls delivery.
- Offline lanes pull overlay data from an Overlay Lookup by frame identity; file overlays have no playback lifecycle
  and their owning runtime closes them explicitly. Live overlays are pushed.

## Plugin System

- Plugins are discovered only through the declared registry, never because their files exist, and must declare the
  host's plugin API version.
- Plugin ids are unique within each plugin type; decoder handler types are unique across loaded decoders for each
  capability.
- Plugin load failures never stop the application. Load, validation and registration failures, including a built-in
  one, are recorded and listed in the Plugins view.
- Plugin dependencies are isolated from the app's: installing or upgrading a plugin never changes the app.
  Management runs from the base app; a change is validated before it activates and applies on restart. A failed
  refresh after an app change starts the base app without external plugins.
- Resolver settings are a declared schema the host renders as a form and as CLI arguments, never plugin UI code.

## Data Pipeline

- Pausing RTSP keeps its connection alive. Stopping is terminal; reopening creates a new source.
- Scene file artifacts are invalid unless their complete identity matches: source content digest (a stat fingerprint
  only spares hashing an unchanged file; where the platform has no change time, the digest decides), decoder name,
  artifact version, Scene version, decode options and store format version. When only the stat fingerprint differs,
  the artifact is accepted and republished with the new one. The fingerprint is taken from the held handle before the
  source is read. If the source changed during the build, a source-indexed store skips persisting and a derived one
  persists without a digest, so a file written during indexing reads as changed on the next open. Cache files are
  replaced atomically, read through the handle that wrote them, private to the user and always rebuildable. An open
  source-indexed store stops serving once its source is rewritten in place; a replaced file leaves it valid.
- Both storage modes persist Scene history built from what lookup serves for each timestamp. Offline history places a
  timestamp-keyed event on the first video frame at or after its sample and a frame-keyed one on its sequence frame,
  and omits events outside the video; an object is present on exactly the frames whose looked-up sample contains it,
  under the lane's matching and sticky selection.
- Whole-file entity filtering judges each distinct set of recorded classification types through the same
  classification policy as Scene entities; it is unavailable unless every filter option declares one, and history
  never fabricates entity data.
- One cache allowance is shared by all open offline sources. Cache pressure never changes frame identity or timing; a
  frame larger than the budget is delivered uncached.

## Export

- Export writes to an owned temporary sibling and replaces the destination only after success. Source/output aliases
  are rejected; cancellation and failure cleanup never delete the destination.
- Exports keep relative source frame timestamps and durations; unreadable frames fail the export. Export borrows a
  frozen filter snapshot and the catalog active when it starts, so later edits cannot alter it.

## Rendering

- Scene rendering produces Drawing Instructions; drawing preparation and submission never read a Scene.
- Overlay draw order is shapes, then text, then labels. Order across entities and within a kind is not preserved, so a
  catalog never relies on one shape covering another.
- Each recipe evaluates once per frame for all its routed entities or relations. A row that fails a demanded check
  emits none of its recipe's output, and a recipe whose evaluation fails emits nothing for that frame; other rows and
  recipes are unaffected.
- Built-in catalogs are read in place from the packaged resources and never copied into the store, written or deleted.
  Every JSON file in the user catalog folder is a user catalog, and startup and listing never overwrite one. The one
  write the app makes is an explicit copy, which validates before atomically replacing its destination. A copy of a
  built-in catalog records it as its origin; that is shown, never acted on.
- The default catalog is the only catalog choice remembered across restarts. Overlay visibility belongs to each view
  or lane, independently of filters and catalog authoring; hiding a feature keeps its inspection and hover data.
- A failed catalog load or reload keeps the last compiled catalog in use and reports the error; a view is never left
  without a catalog.

## Settings And Shortcuts

- Domain code takes settings as values; only the settings service, wired at bootstrap, reads and writes them.
- Settings apply on OK. Settings that need a restart carry one shared marker, and saving one offers to restart after
  the exit-time saves. Quick Setup is the one exception.
- Saves validate every editable field, replace the configuration atomically, and only then notify; a failed save
  changes nothing. Startup storage folders stay active until restart, except recovery copies and recent workspaces,
  which follow the newly saved folder.
- Settings live in one immutable state replaced whole; consumers subscribe to changes, never poll.
- On/off overlay preferences are one closed set; adding a member adds it to the View menu, Settings and the config.
- All shortcuts are defined in one table; persistence is override-only, so no overrides means all defaults apply.
