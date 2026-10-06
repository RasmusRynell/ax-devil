# Diagnostics

This package owns rendering diagnostics, source metrics, performance recording, and exception handling.

- `render_metrics.py` owns typed frame/paint observations, measurement labels, viewer lifetimes,
  submission progress, and bounded recent timing distributions.
- `metrics_store.py` retains typed source observations with individual timestamps and stable instance identities.
- `metrics_gate.py` controls capture through Debug > Collect Debug Metrics.
- `dashboard_snapshot.py` combines rendering, source, and video-cache state for the UI and JSON export.
- `debug_window.py` provides viewer selection, a timing graph/table, workload details, and separate
  source/cache and recording tabs. It updates existing widgets every 500 ms.
- `history_chart.py` plots timing operations with spatial selection and keyboard navigation.
- `paint_inspection.py` builds evidence and preceding-paint comparisons; `paint_inspector.py` presents that evidence.
- `trace_recorder.py` / `trace_controls.py` own independent Python stack sampling and export.
- `exception_handler.py` and `plugin_window.py` own exception reporting and plugin diagnostics.

How to use the window, investigate a spike and record a timeline: [Diagnostics runbook](../../../../docs/runbooks/diagnostics.md).

## Measurement definitions

These are the timing boundaries behind the dashboard. All durations are CPU-side observations; GPU execution,
texture upload and screen presentation are never measured or inferred.

**Drawing preparation.** `CachedSceneOverlay` measures filtering where the filtered Scene is built, including work
the presenter requests for the entity inspector before painting; the next drawing retrieval reports that pending work
once. Drawing preparation measures catalog evaluation plus geometry conversion, culling and text shaping. A cached
retrieval has no generation duration (`None`, shown as `—`), not a misleading near-zero build time. Each result also
reports filter and drawing cache reuse, input and remaining entity counts, primitive counts by kind, and the inputs
that caused a rebuild, found by comparing against the previous successful cache key (filter configuration and state,
target size, catalog identity). Counts belong to the prepared drawing; metrics read them directly.

**Paint samples.** `VideoFrameRenderer` records one atomic `PaintSample` after Quick GUI preparation: backend, source
frame and overlay identities, overlay reuse, target dimensions, total CPU paint duration, the four disjoint stages
(video-image handoff, drawing preparation, Qt item binding, remaining CPU time), available versus submitted primitives
(zero when opacity suppresses drawing), and the overlay's generation metrics. Total covers GUI-side image handoff,
path and text preparation, retained-item updates and explicit Shape polishing. It excludes texture uploads, the rest
of native scene graph synchronization and render submission, GPU execution, metrics publication, the info HUD, child
widgets, decoding and screen presentation. Generation is nested inside preparation, and filtering may precede the
paint, so neither is added to the four paint stages.

**Submissions and pacing.** `RenderMetricsStore` owns viewer registration and bounded histories (600 paints, recent
10-second statistics window). Submitted and last-painted identities are distinct. Only the first paint of a submission
gets submission-to-paint latency. Repainting an unchanged source identity counts separately from a new-frame paint.
Submissions replaced before painting count as superseded, not as decoder drops. Rates cover the observed interval, or
the retained interval when the sample limit truncates it. A new-frame interval can be split into pre-submission time,
submission wait and paint time only when submission was measured and does not overlap the preceding new-frame
completion; pre-submission time may include deliberate pacing, pauses, seeks or source work.

**Source context.** Source state uses typed `SourceObservation` values with independent monotonic timestamps; a
snapshot copies each source's fields without claiming they belong to one frame. `RenderMetricsStore.record` copies the
viewer's linked source observations after paint timing ends, outside the measured CPU paint, and records the capture
time separately; immutable copies stay in the bounded paint history so later updates cannot rewrite spike evidence.
Superseded submissions since the previous paint are attached to each observation. Overlay offset is signed
frame-minus-overlay time in milliseconds, unavailable without a match. File lookup statistics count unique
timestamps, not calls, and outcome sets can overlap after policy changes.

**Export format.** Diagnostics JSON schema version 6 contains the snapshot capture time (`captured_at`), independently
timestamped source observations, viewer/source links, cache budget and reserved bytes, paint intervals, rebuild
reasons, per-paint source context, the inspection selection, and recent paint history. Paint, submission and source
observation clocks are process-monotonic seconds; frame timestamps remain source microseconds, never wall-clock
latency. The top-level UTC timestamp identifies export time.

## Performance recording

`trace_recorder.py` samples Python stacks across existing threads at a target 100 Hz using
`sys._current_frames()`. It excludes its own sampler thread. Frame names and stacks are interned;
snapshot records are appended to a temporary file instead of overwriting a circular buffer.
Export preserves the whole session's retained samples and the recording's start-to-stop duration.

This is an in-process sampler: the GIL and scheduling can delay it, short calls can be missed,
and native Qt/FFmpeg internals are not visible. Waiting threads are included. Samples are stack
observations, not measured function entry/exit times or CPU usage.

`trace_controls.py` owns the controls. Recording is independent of the metrics switch and inactive
until started. A capture error stops sampling and makes its partial status visible; completed samples
remain exportable. Canceling the save dialog or an export failure retains the stopped recording.
Exports replace the chosen destination only after a complete temporary export has been written.
Starting another recording replaces the previous one; closing the diagnostics window joins the sampler and
discards its temporary storage. Saved files remain on disk. Source paths and function names are
collected; source contents, local variables, arguments, and return values are not.

The version-1 `ax-devil-stack-samples` JSON format stores millisecond timestamps relative to recording
start, nominal interval, stop duration, frame/stack dictionaries, thread labels, and ordered snapshots
of `[time_ms, [[thread_id, stack_id], ...]]`. Thread identifiers are recording-local strings. A changed Python `Thread` object or an observed
absence starts a new identity, preserving prior labels when native identifiers are recycled.
For Qt/native threads without a distinct Python `Thread` object, reuse entirely between samples
cannot be detected; their identity is best-effort. Disk use grows with
recording duration; in-memory dictionaries grow with distinct frames and stacks, not snapshot count.
