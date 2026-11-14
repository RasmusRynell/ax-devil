# plugins

Installed programs and the handlers they provide: file and lane decoders, playlist resolvers and tasks, plus views and
commands that need no program. The contract authors follow is
[the write-plugin reference](../../.agents/skills/write-plugin/reference.md); this file holds what the host does and
why. `load(registry, reserved)` reads `plugins.json` and returns one entry per listed manifest, failed ones included
so they can be listed; a `LoadedPlugin` holds one `PluginDecoder` per decoder handler, each the `Decoder` that
`Handlers::with_decoders` adds beside the built-ins, one `PluginResolver` per resolver handler, added by
`with_resolvers`, and one `PluginTask` per task. The handlers of a plugin share one `Program` (program and arguments,
environment, idle timeout, settings, code identity). The scene store drives it like any decoder and owns grouping, ordering and
caching.

- **Manifest is validated whole** (`manifest.rs`, `fields.rs`): an error names the field by its path, such as
  `handlers[1].options[0].kind`. A handler id must be new across plugins unless it is a view or a command, which only
  differ within their plugin (`Reserved`). The registry
  (`registry.rs`) is written only by install, update and remove, atomically and private to the user, and keeps keys it does
  not know.
- **Identity.** `code_hash` is SHA-256 over the manifest, the plugin's settings and the files its `code` names, re-read
  only when a size or modification time changed. A settings change is a new identity, so that plugin's lanes rebuild. A plugin whose files cannot be hashed is not run, so nothing is cached under a
  missing identity.
- **One run** (`run.rs`): own process group, private empty working folder as cwd and `TMPDIR`, an environment cleared to
  `PASSED_ENV` plus the manifest's `env` (no `AX_DEVIL_*`, which the manifest refuses). The request (the handler, the path and
  validated options, no frame size) is written on a thread so a plugin that never reads stdin cannot block the host. A reader
  thread feeds 64 KiB chunks through a bounded channel to the line reader, so a fast plugin is held back by the
  store, not buffered. A line over 16 MiB is refused before it is buffered.
- **Records** (`input: records`, `Process` in `run.rs`, the loop in `decoder.rs`). A live session is one `decode` over an
  input with no path, so a records decoder is the live capability (`reads_live`, when `time` is a basis). The same
  process is fed one record at a time through a writer thread and the next record goes out only after `done`: the host's
  bridge queue holds the rest, so there is no buffering of its own. A process that ends after an answer costs the one record
  it was on and is started again with the next; one that ends before its own first answer (a restarted one too) fails the session, so a broken
  plugin is not started once per document. The idle timeout runs only while an answer is owed.
- **Lanes, tasks, views, commands.** A `decoder` with `input: lanes` is a derived lane: its input file is the host's
  bundle (`handler_api::Bundle`, which lists each input lane file by key with its hash, and the video), and the
  request's `input` is `{lanes: {key: path}, video?}`. A `task` takes the same input and options, produces no Scenes
  and answers with `progress`, `warning`, `error` and `done` lines; dropping the run cancels it. A `view` names
  a page in the plugin's folder and a `command` names an own lanes decoder, task or view; either whose reference does
  not resolve is `skipped` with the reason, like an unknown kind. A plugin of only views and commands has no `program`.
- **Settings.** The top-level `settings` form is stored in the plugin's registry entry and sent as `settings` in every
  decode and task request. Nothing handed to a program is expanded: a setting or option holding `$HOME` arrives as that
  text. A resolver's request already uses `settings` for its own form, so it does not carry the plugin's.
- **Every run ends the same way.** The group is killed whether the plugin finished, failed, was cancelled or went
  idle (no output for `idle_timeout_s`), then the child is waited for and the working folder removed. A grandchild left
  behind never outlives the lane; output already written is still read, for up to 2 s. Windows has no process group here: only the plugin process itself is killed.
- **stderr** is drained on its own thread so a full pipe cannot stall the plugin; the last 64 KiB is kept, the last
  20 lines (2 KiB) join a failure reason, and it is logged at DEBUG only because it may echo the user's data.
- **Scan and resolve** (`resolver.rs`, `resolved.rs`) run the same process on the same runner, with the lines
  collected instead of fed to a sink. Each line is checked as it arrives (absolute paths, unique entry names, unique
  slots and labels per entry) and a bad one fails the operation naming its number; the result is used only after exit
  0. An `error` line becomes a `UserError` at the setting `field` or the host `path` it names. Manifest fields share
  `handler_api`'s `Field` in memory but are read by `fields.rs` with their paths, as the wire shape is nested.
- **Failure is the host's call.** `decode` returns `Err` for an Incomplete end; the scene store knows whether
  samples arrived and marks the lane Incomplete (some) or failed (none). Bad lines never fail a run; they are counted
  into one warning (`records.rs`).
- Process limits are not applied: a plugin can use all the CPU and memory the user has.
