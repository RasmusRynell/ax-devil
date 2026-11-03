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
running work is interrupted at its next poll, and nothing more is delivered.

## Replaceable requests

`LatestRequest(owner)` owns one consumer's current request, whether submitted to a job scope with `submit` or
started through a callback-based service with `start`. Replacement cancels the old request; only current progress,
results and errors reach the consumer. `pending` ends before a completion callback runs, including a synchronous
cached result that starts another request. Close the slot before its job scope during owner cleanup; owner
destruction also closes it.

The slot owns pending delivery, not completed data. A form clears its input-derived results when inputs change;
a viewer may keep its last good output while its replacement loads. Those decisions stay with the consumer.

## Cancellation

- The owner of work creates its `CancelToken` (`core/cancellation.py`) and calls `cancel()`; for a job, the runner
  creates it and `job.token` is the work's view of it. Work never cancels its own token.
- Work polls the token between operations: `for path in job.token.polled(directory.iterdir())`,
  `job.token.raise_if_cancelled()`, and `job.token.wait(seconds)` instead of `sleep`. `JobContext.report` polls too.
  A poll of a cancelled token raises `Cancelled`; there is no way to ask whether a token is cancelled.
- `Cancelled` is a `BaseException`, so an `except Exception` never swallows it. Work releases what it holds in
  `finally`; only the code that started the work catches `Cancelled`, to stop quietly. The exception is work whose
  partial outcome must be freed on the GUI thread, such as opened sources: it catches `Cancelled` itself and returns
  what it holds, so `on_discard` releases it like a result that finished after the cancel.
- A cancelled job delivers nothing, whether it was interrupted, finished anyway or never started. Only a result that
  finished anyway reaches `on_discard`. `Cancelled` raised when the job was not cancelled, such as from another token,
  goes to `on_error`, so an owner never waits for an outcome that does not come.
- Code with nothing to cancel, such as the command line or a test, passes `NEVER`, a token that cannot be cancelled.

## Rules

- Work receives the values it needs when submitted and never reads widgets, Qt objects or state the GUI thread changes.
- Use timeouts where blocking APIs support them and poll the token between operations. Cancellation is cooperative
  and cannot interrupt a call; file APIs may have no timeout, so potentially slow filesystem work uses its own pool.
- Results and progress are plain values. A result that owns something that needs releasing when nobody wants it, such
  as a running source, comes with an `on_discard` that releases it on the GUI thread; it runs only for a result whose
  job was cancelled before delivery, even if work already finished, and must not hold the owner.
- Work that is slow or must run one at a time, such as opening files in order, asks for its own pool with
  `background_jobs("name", max_workers=n)`, so it neither delays nor is delayed by the shared one.
- Plugin settings forms use the `jobs` scope of `PlaylistResolverWidget`, which ends with the widget and runs on the
  `"plugins"` pool, so a slow folder scan never delays the shared one; plugins import
  `JobContext` from `ax_devil.modules.plugin_system` for their work's signature.
- Ending a scope stops delivery, not effects. Work that changes something outside the app, such as installing plugins,
  keeps its own runner.
- Streams and other long-running producers own their threads.
- Workers are daemon threads that stop when no job is left, so quitting never waits for a job. Everything a job holds
  is released on the GUI thread.
