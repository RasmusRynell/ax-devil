# converters

Installed programs as file decoders. The contract authors follow is
[the write-plugin reference](../../.agents/skills/write-plugin/reference.md); this file holds what the host does and
why. `load(registry, reserved)` reads `plugins.json` and returns one entry per listed manifest, failed ones included
so they can be listed; `ConverterDecoder` is the `Decoder` that `Handlers::with_decoders` adds beside
the built-ins. The scene store drives it like any decoder and owns grouping, ordering and caching.

- **Manifest is validated whole** (`manifest.rs`): an error names the field. The registry (`registry.rs`) is written
  only by install and remove, atomically and private to the user, and keeps keys it does not know.
- **Identity.** `code_hash` is SHA-256 over the manifest and the files its `code` names, re-read only when a size or
  modification time changed. A converter whose files cannot be hashed is not run, so nothing is cached under a
  missing identity.
- **One run** (`run.rs`): own process group, private empty working folder as cwd and `TMPDIR`, an environment cleared to
  `PASSED_ENV` plus the manifest's `env` (no `AX_DEVIL_*`, which the manifest refuses). The request (path and validated
  options, no frame size) is written on a thread so a converter that never reads stdin cannot block the host. A reader
  thread feeds 64 KiB chunks through a bounded channel to the line reader, so a fast converter is held back by the
  store, not buffered. A line over 16 MiB is refused before it is buffered.
- **Every run ends the same way.** The group is killed whether the converter finished, failed, was cancelled or went
  idle (no output for `idle_timeout_s`), then the child is waited for and the working folder removed. A grandchild left
  behind never outlives the lane; output already written is still read, for up to 2 s. Windows has no process group here: only the converter process itself is killed.
- **stderr** is drained on its own thread so a full pipe cannot stall the converter; the last 64 KiB is kept, the last
  20 lines (2 KiB) join a failure reason, and it is logged at DEBUG only because it may echo the user's data.
- **Failure is the host's call.** `decode` returns `Err` for an Incomplete end; the scene store knows whether
  samples arrived and marks the lane Incomplete (some) or failed (none). Bad lines never fail a run; they are counted
  into one warning (`records.rs`).
- Process limits are not applied: a converter can use all the CPU and memory the user has.
