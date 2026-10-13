# Background Jobs

`BackgroundJobs` runs occasional finite work, such as a network scan, away from the GUI thread. The application's workers are pools returned by `background_jobs(pool)`: the default `"shared"` pool,
which consumers use by default, and pools that slow or one-at-a-time work asks for by name and `max_workers`; tests
pass their own instance.

## Use

An owner takes a `JobScope` with `jobs.scope(owner)` and submits plain work to it:

```python
self._jobs = jobs.scope(view)
self._jobs.submit("network scan", partial(scan, targets), on_result=self._show, on_error=self._fail)
```

Exactly one of `on_result` and `on_error` runs on the GUI thread, and `on_progress` receives what the work reports.
`submit` returns a function that cancels that one job. Closing the scope cancels all of its jobs; the owner closes it in
`cleanup()`, and it closes when the owner is destroyed. Cancelling never waits: work not yet started never runs,
running work sees `JobContext.cancelled`, and nothing more is delivered.

## Rules

- Work receives the values it needs when submitted and never reads widgets, Qt objects or state the GUI thread changes.
- Every blocking call in work has a timeout; cancellation is cooperative and cannot interrupt a call.
- Results and progress are plain values. A result that owns something that needs releasing when nobody wants it, such
  as a running source, comes with an `on_discard` that releases it on the GUI thread; it runs only for a result whose
  job was cancelled while it ran, and must not hold the owner.
- Work that is slow or must run one at a time, such as opening files in order, asks for its own pool with
  `background_jobs("name", max_workers=n)`, so it neither delays nor is delayed by the shared one.
- Plugin settings forms use the `jobs` scope of `PlaylistResolverWidget`, which ends with the widget; plugins import
  `JobContext` from `ax_devil.modules.plugin_system` for their work's signature.
- Ending a scope stops delivery, not effects. Work that changes something outside the app, such as installing plugins,
  keeps its own runner.
- Streams and other long-running producers own their threads.
- Workers are daemon threads that stop when no job is left, so quitting never waits for a job. Everything a job holds
  is released on the GUI thread.
