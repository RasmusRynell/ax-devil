# 03 Runtime

Owns: concurrency guarantees, lifecycle, memory, time injection, the error model and logging. Other files link here.
Sources: `docs/domain/invariants.md#ui-lifecycle`, `#display-rendering`, `#synchronization`;
`background_jobs/README.md`; `data_sources/README.md`.

## Concurrency guarantees (MUST)

| Guarantee | Reference mechanism (current) |
|-----------|-------------------------------|
| The UI never blocks on file, network, decode, compile or index work, and never nests its event loop to wait | Worker threads; results delivered by queued signals |
| Finite work runs as tasks owned by a scope; closing the scope cancels its tasks and stops delivery | `BackgroundJobs` and `JobScope` |
| Cancellation propagates through every layer a task calls and cannot be swallowed by generic error handling; a cancelled task delivers nothing | One cooperative token polled between operations; `Cancelled` is a `BaseException` (`src/ax_devil/core/cancellation.py`) |
| Tasks receive all inputs at submission and never read UI or UI-mutated state | Plain values passed to `submit` |
| A newer request for the same consumer supersedes the older; only current results arrive | `LatestRequest` |
| Slow or ordered work never delays unrelated work | Named pools (`shared`, `plugins`, `render-catalogs`, `render-catalog-mutations`, `overlay-analysis`) |
| Streams own their producers; work with external effects (plugin install) finishes regardless of who still listens | Own threads and runners |
| Every resource has one owner at a time; hand-offs are explicit; an unclaimed result's resources are released | `EntryMedia` ownership until `release_media`; `on_discard` |
| A resource is destroyed on the executor it requires (UI objects on the UI thread, GPU objects in the renderer phase) | GUI-thread deletion; Qt scene-graph phase |
| Memory management never pauses frame delivery | Python: GC frozen after startup and collected on a GUI timer (**drop**) |
| Cancelling background warm-up is immediate and leaves no temp files | Next-entry warm-up in a child process killed by process tree (a thread is fine natively, D3) |

## Real-time delivery (MUST)

The rules are in `docs/domain/invariants.md#display-rendering`, `#frame-identity` and `#synchronization`
(translations in [04](04-domain-rules.md#translate)). Live queues, designing out the CHANGELOG known limit ("high live
frame rates or bursts can exhaust the frame buffer"):

- Every queue is bounded; its owner defines the overflow policy, and every drop is counted per stage in the timing
  diagnostics. A burst never stalls delivery.
- Encoded video: codec-aware discard, then resync at the next decodable keyframe; exercised by the burst test.
- Payload decode is lossless: its bounded input queue overflowing is a reported lane error ("overlay decoder behind"),
  never a silent drop, because LV-07 needs every event and decoders may be stateful ([05](05-formats-and-extensions.md#decoder-contract)).
- Drop-oldest (latest wins) applies only after decode, to independently consumable frames and synchronized pairs.
- After "overlay decoder behind": the lane stays in that error state until the queue drains, the decoder is reset
  as on reconnect, and the Events list shows a gap marker; the burst test asserts all three.
- Truncation of the live event buffer is visible to the user.
- Tools that follow playback do no work while invisible (`video_viewer/README.md#rules`).

## Time and scheduling (MUST)

- Inject a clock service (monotonic time for scheduling, wall/UTC time for stored times such as recents and
  bookmarks), a timer service and the task executor. No module reads a clock or sleeps except through them. This
  covers debounces, the recovery-copy rewrite, the snapshot rate limit, the stall detector and clock-following playback.
- Policy tests use a virtual clock and a controllable executor; inline execution only where scheduling is irrelevant
  ([09](09-testing.md#reliability-must)).

## Lifecycle

Startup order (*current:* `Application.start()` in `src/ax_devil/app.py`):

1. If D1 keeps per-installation plugin selections, resolve the active one.
2. Logging to console; load settings; ensure storage folders; logging to file.
3. Discover and register plugins; record failures.
4. Create the UI; apply theme and text size; install shortcuts.
5. Create the catalog manager.
6. Show the main window and launch: CLI items → new Untitled workspace; `open FILE` → that workspace; plain launch →
   restore the recovery copy if it is the newest recent, else start panel.
7. Start the background scan if enabled; open start-up windows one at a time (Quick Setup on first start, then the
   catalog window for `ax-devil catalog`).

- **MUST** never prompt at startup (`docs/domain/invariants.md#workspace-and-viewers`). Plain launch, recovery and
  opening a workspace never connect streams; explicit live intake (CLI `live`, Add actions) may; an existing stream
  row needs Start.

Shutdown:

- **MUST** clean up every viewer through its contract; renderers release resources before their owners.
- **MUST** wait for retiring live sources within one bounded deadline after the UI loop ends, so transport cleanup
  completes, including removing temporary device configuration (LV-08). *Current:* `DataSource.wait_for_retired()`.
- **MUST** keep the recovery copy, save shortcut overrides, relaunch if a restart was requested.

Live sources: connect without blocking play; report retried failures and failures needing the user separately with
reasons; ignore callbacks from replaced sources (`docs/domain/invariants.md#ui-lifecycle`). DataHub idle silence is
not an error (`data_sources/README.md#live-transports`).

## Memory (MUST)

- Bounded memory per component, with limits stated in the owning module's docs. Sources:
  `docs/settings.md#video-cache-memory`, `data_sources/README.md#offline-frame-cache`,
  `#file-overlay-providers` (*current:* two most recent decoded Scenes per provider, sized for Python GC pauses;
  re-derive for the new runtime).
- Cache pressure never changes frame identity or timing; a frame larger than the budget is delivered uncached.
- Cache identity and invalidation: `docs/domain/invariants.md#data-pipeline`. Caches are private, versioned and
  rebuildable; never load caches from other users.

## Performance

The repo measures (DT-03) rather than stating budgets; D9 sets them. Proposal, not a repo fact: offline 1080p30 with
four lanes and Standard, and live 1080p30 with one overlay, at full rate; overlay preparation under 4 ms for 100 objects.

## Error model (MUST)

- One user-facing error value (reason plus location: file, item, field or JSON path) produced by the layer that
  detects the failure and shown where the user acted: row, dialog, banner or viewer status. Logs are for diagnosis.
- Failure behaviors to port, each defined at its source:

| Failure | Source |
|---------|--------|
| Item resolution, unreadable workspace or item | `docs/architecture/workspace.md#resolution`, `#file-format` |
| Malformed decoder record, whole-file load warnings | `.agents/skills/write-plugin/SKILL.md#writing-it-well` |
| Recipe evaluation, catalog load | `docs/domain/invariants.md#rendering` |
| Plugin load | `docs/domain/invariants.md#plugin-system` |
| Settings save | `docs/domain/invariants.md#settings-and-shortcuts` |
| Export | `docs/domain/invariants.md#export` |
| Whole-file analysis | `video_viewer/README.md#rules` |
| Uncaught exception: log, offer continue or close | `application_shell/exception_handler.py` |

## Logging (MUST)

- One logger factory per module; app code never prints. *Current:* `get_logger`, enforced by `scripts/check_logging.py`.
- Levels DEBUG, INFO, WARNING, ERROR from a global CLI flag; console plus a rotating file in the logs folder.
- Toolkit and library logs go to the same sink; repeated per-frame errors are rate-limited.
- Credentials and URL userinfo are redacted before logging. *Current:* logs may contain them (`docs/settings.md#configuration-and-local-data`).
