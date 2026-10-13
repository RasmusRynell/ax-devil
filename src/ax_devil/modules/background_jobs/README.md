# Background Jobs

`BackgroundJobs` runs occasional finite work, such as a network scan, away from the GUI thread. The application uses
one instance, `background_jobs()`, which consumers use by default; tests pass their own.

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
- Results and progress are plain values. Work that opens sources, or anything else that needs releasing when nobody
  wants it, keeps its own owner, like `EntryOpening`.
- Ending a scope stops delivery, not effects. Work that changes something outside the app, such as installing plugins,
  keeps its own runner.
- Streams and other long-running producers own their threads.
- Workers are daemon threads that stop when no job is left, so quitting never waits for a job. Everything a job holds
  is released on the GUI thread.
