# Domain Invariants

Rules that code in more than one module must keep and that no type, validation or test enforces. Each holds
whenever its feature exists. Terms are defined in
[CONTEXT.md](../../CONTEXT.md); how things fit together is in the [architecture overview](../architecture/overview.md).

Not here: user-visible behavior, rules one module keeps to itself, workarounds and measurements (see the placement
rule in [AGENTS.md](../../AGENTS.md#where-a-fact-belongs)). A rule with no test guarding it is a test gap that the
implementing module closes.

Rules for Workspace content, viewers, the sidebar, UI lifecycle, plugins and settings are in [app invariants](app-invariants.md).

## Coordinates

- Bounding boxes, polygons and points use normalized `[0,1]` image space. Catalog lengths resolve against the lane's
  cell, not source-video dimensions; a length in pixels keeps its size on screen under page zoom.

## Display Rendering

- Video and overlay data travel separately and are joined by frame in the frontend; queued updates may coalesce to the
  latest frame. Offline delivery presents only the newest frame queued for the UI; seeks and frame steps still present
  the requested frame.
- Offline playback follows the clock: delivery that falls behind skips to the frame due now instead of slowing down;
  the final frame is always delivered. The viewer owns that clock; the backend supplies frames and never reads one.
- GPU resources are created and released only in the renderer's resource phase.
- Display and export run the same presentation code; pixels differ only by documented tolerances.

A seat holds at most one stream per viewer; a ticket request carries prior viewer releases, which commit before and independently of its admission. Without a successor request, playback socket closure releases the stream.

## Offline Delivery

- A viewer keeps exactly the frames inside the window of its last report and evicts the rest. The window is
  `floor(buffer budget / frame bytes)` frames, fixed per video, so it never evicts inside it.
- The backend applies every window report to its record of what a viewer holds, in order, never coalesced; only
  scheduling uses the newest. A frame that leaves the window and returns is resent.
- A frame's bytes depend only on its identity, so a viewer keeps any arrival inside its window.
- A viewer shows one video: its lanes share its frames and clock, frames travel once per viewer, and only overlay
  data is per source or lane.
- A lane draws only the marks of the sample its Frame Map assigns to the frame, never another's. In play a
  lane without data shows a loading state and the video is not held; after a seek or step the frame waits up to
  about 250 ms for ready lanes; export always waits. Loading, failed and hidden lanes never hold a frame back.
- Sample Items, Frame Maps and inspections pass the authority gate of the frame they rank with; items and maps cover
  the window it allowed, never the viewer's raw request. An inspection is answered only for a sample the viewer holds,
  else as unavailable, ungated. The backend alone decides what a viewer holds, within its byte budget; evictions and
  the map window are never gated.

## Frame Identity

- Every frame has a sequence id and a timestamp: signed 64-bit integer microseconds on the media clock, a Scene
  datetime's time since the Unix epoch. Live frames carry device capture time; an offline video's first frame is 0
  and its frames take their times from container presentation timestamps, never from FPS, so timestamp-keyed files for
  a recording count from the epoch and variable frame rates work. Units convert in one place.
- Decoders emit records and know nothing about the video. A lane's match basis is host policy, chosen from the bases
  its decoder declares and starting at its declared default; live is matched by time. Frame: a record belongs to the
  frame numbered by its key, for a time-keyed file its position (record k, frame k). Time: exact timestamp first,
  then the latest previous record within the configured tolerance; a future one is never selected early. Keys are
  never synthesized from FPS. A decoder declares coverage: in a Dense file a frame without a sample is empty and
  sticky never crosses it; a Sparse one says nothing about it.
- With sticky overlays enabled, offline playback and export select the latest sample at or before the requested time,
  regardless of navigation history. Age is measured from the sample's own timestamp.
  The timeout includes its boundary and also expires a tolerated sample; no timeout means unlimited. Tolerance
  separates normal matches from retained samples, drawn at the retained opacity, and does not limit retention. With
  sticky disabled, unmatched frames have no overlay. Empty scene updates replace previous geometry; missing updates
  may retain it.
- Live synchronization consumes the newest queued sample at or before the frame within the lane's sync tolerance,
  as files do. Stale samples are discarded before persistence; late older ones cannot replace newer state.
- File overlay packets keep the matched sample timestamp, and its sequence when known; the requested frame stays in
  lookup metadata.
- Alignment diagnostics compare on the lane's match basis.

## Scene Model

- Scene is the exchange format for decoded world-model data: all decoders produce it; filtering, inspection and
  rendering consume it.
- Published Scenes are snapshots: publish a replacement instead of mutating a delivered one; unchanged identities
  may reuse their encoded Sample Item.
- A classification type is a dynamic string; known values are only a convenience. Routing by classification is catalog
  policy: UI code never switches on classification strings.
- Score and confidence are separate: a decoder sets what its source provides and never derives one from the other or
  a default; a missing value stays missing and the sample is kept. Both are any finite number shown plain; a bar for a
  value in 0..1 is a display hint, not a range requirement. The primary classification ranks scored before unscored,
  then by score, then by confidence.
- A relation is a directed link between entity ids. A relation with a missing endpoint is valid but not rendered.
- An observation's source data holds what the source supplied verbatim; its debug data holds what the decoder
  computed. Both hold only schema value types (null, bool, number, string, list, map); inspection shows and queries
  them under Debug, each field with its origin. Scene-level debug data is decoder-owned frame diagnostics, never
  displayed and without origin. Filtering, rendering, synchronization and hit-testing never read any of them.
- Changing the Scene contract bumps its version: major when breaking, including stricter value requirements, minor
  when additive. Scene datetimes are timezone-aware. A decoder declaring another major, or a newer minor, fails to
  load; the host normalizes accepted older-minor output before caching.

## Synchronization

- Live sync pairs data by capture time, not arrival time. While the feed has an overlay, a live frame waits until its
  overlay is settled or a bounded wait ends (D28), and frames leave paced by the video: one per arriving frame, or on
  a timer when none arrives; no future sample shows early. Sharing an RTSP connection does not pair arrivals; both keep their capture timestamps.
- Every live queue is bounded with an owner-defined overflow policy; every drop is counted in timing diagnostics.
  Encoded video is discarded codec-aware and resyncs at the next keyframe. DataHub and embedded metadata decode
  losslessly: overflow is a lane error ("overlay decoder behind") that holds until the queue drains, resets the
  decoder and leaves a gap marker in Events. MQTT keeps only the newest waiting message; a replaced one is only
  warned about, with its topic. A burst never stalls delivery.
- Offline lanes pull overlay data from an Overlay Lookup by frame identity; file overlays have no playback lifecycle
  and their runtime closes them explicitly. Live overlays are pushed.

## Data Pipeline

- Pausing RTSP keeps its connection alive. Stopping is terminal; reopening creates a new source.
- Scene file artifacts are invalid unless their complete identity matches: source content digest (a stat fingerprint
  only spares hashing an unchanged file), decoder name (for a plugin, the hash of its manifest and code files),
  artifact version, Scene version, decode options and store format version. If the source changed during the build, the artifact is persisted without a digest, so the
  next open sees the file as changed. Cache files are private and always rebuildable.
- Samples with one start key are one group, merged or replaced as the decoder asks. A key below an earlier one freezes
  the stream: the rest is sorted at the end into a new generation with its own numbering and revision.
- While a file is read, a Frame Map cell is sent only for a frame the samples so far decide, and a sent cell never
  changes; the rest stay pending. A read ends Complete, or Incomplete with its reason and what it read.
- File history is built at ingest and judges an object on every class it was recorded with, through the lane's
  class toggles and the same classification policy as Scene entities (a car that became a bus matches both); a policy
  without options keeps every object.
- An object is present on exactly the frames whose looked-up sample contains it, under the lane's matching and sticky
  selection. An event sits on the first video frame at or after its timestamp-keyed sample, or on its frame-keyed
  sequence frame, and is omitted outside the video.
- File scope lists a lane's whole file, once its ingest has ended and only to a viewer that chooses its own frames: one
  following the Presenter View never sees beyond what it shows. Frame and File scope are never combined.
- One cache allowance is shared by all open offline sources. Cache pressure never changes frame identity or timing; a
  frame larger than the budget is delivered uncached.

## Export

- Export writes a temporary sibling and replaces the destination only on success; an output that is also an input
  is rejected; cancel and failure never delete the destination.
- Exports keep source frame timestamps and durations and fail on an unreadable frame. They freeze each lane's catalog
  document, style, policy and source revision at start.
- Only an Owner exports, one job per backend; closing its socket cancels it.

## Rendering

- The frontend evaluates, lays out and picks per lane; the backend never evaluates a catalog or rasterizes. Evaluation
  turns a Sample Item into Marks; layout and painting read only Marks. A viewer's viewport (one for all its lanes) is
  part of the view, changed only by its own command; selection (an entity id, shown in every lane of its source, kept
  across seeks) is client view state, never sent.
- Overlay draw order is the catalog's layer order; inside a layer, rows in sample order, each row's marks in listed
  order.
- A mark whose evaluation or layout fails at runtime (a null required field, a non-finite result, an invalid length
  combination) leaves out every mark its entity row or relation has in every shown layer; the row is counted once,
  against the first failing mark. Other rows are unaffected, and evaluation never throws.
- Built-in catalogs are read in place and never copied into the store, written or deleted.
  Every JSON file in the user catalog folder is a user catalog, and startup and listing never write one. The app
  writes a catalog only as a copy of one it serves, never over an existing file, and deletes only a user catalog the
  user chose.
- The default catalog is the only catalog choice remembered across restarts. Overlay visibility belongs to each view
  or lane, independent of filters and catalog authoring; hiding a feature keeps its inspection and hover data.
- A failed catalog load or reload keeps the lane's last drawn catalog in use and reports the error; a lane is never
  left without a catalog.
