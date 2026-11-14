# Plugin reference

The one specification of the plugin contract (protocol 3). Code that enforces it: `crates/plugins`. The model plugin
is `fixtures/plugins/python-tracks`; a second, in POSIX shell and awk, is `fixtures/plugins/shell-times`. The model
resolver is `fixtures/plugins/python-runs`.

A plugin is a program and a manifest that lists the **handlers** it provides, of kind `decoder` (an overlay file in,
Scenes out) or `playlist_resolver` (settings in, playlists out), or `task` (lanes in, a side effect out). A `view`
or `command` needs no program: [views.md](views.md). One handler call is one run of the program.

## Manifest `ax-devil-plugin.json`

A JSON object in the plugin's folder. Unknown fields, a wrong type or a missing required field refuse it, naming the
field by its path, such as `handlers[1].options[0].kind`.

| Field | Required | Meaning |
|-------|----------|---------|
| `manifest` | yes | `2` |
| `id` | yes | The plugin id: 1-64 characters of `a-z 0-9 . _ -`, starting with a letter or digit. Must differ from every built-in and installed plugin id |
| `name`, `version` | yes | Shown to the user; `version` is free text |
| `protocol` | yes | `3`; any other value does not load |
| `command` | yes, unless only views and commands | Program and arguments. A program containing `/` is relative to the folder and must be executable; otherwise it is looked up on `PATH`. Later items that name a file or folder of the plugin become absolute; items starting with `-` are passed as written. Nothing is appended: the request carries the path |
| `env` | no | Names of environment variables to pass through besides the standard few. `AX_DEVIL_*` is refused |
| `code` | no | Paths in the folder (files or folders, no `..`) whose bytes identify the plugin; default the whole folder. Dot-names and `__pycache__` are skipped; at most 20,000 files and 256 MiB |
| `idle_timeout_s` | no | 1 to 86,400; default 120. Seconds the plugin may print nothing before it is killed |
| `settings` | no | A list of fields, below, of kind `text`, `file` or `folder`; the user's values are sent in every `decode` and `task` request as `settings`, literally (no `$NAME`), and are part of the code hash |
| `handlers` | yes | A non-empty list of handlers, below |

Every handler has `kind` and `id` (same characters as the plugin id; it may equal the plugin's own id, and must
differ from every other handler id, built-in or installed, of any kind; `--handler-type` takes it) and `name`. A
`kind` this ax-devil does not know is skipped, unread and without an id, and listed under the plugin as needing
a newer ax-devil; the plugin's other handlers work, and a plugin left with none fails.

Decoder handler (`"kind": "decoder"`):

| Field | Required | Meaning |
|-------|----------|---------|
| `input` | yes | `"path"`: the request carries the overlay file's path. `"records"`: the request carries none and the records follow on stdin one at a time ([Records](#records)); a file is read line by line, and with `time` in `bases` the decoder also takes live sources. `"lanes"`: a derived lane computed from lanes of the entry ([Lanes](#lanes)); needs `lanes` and takes `video` |
| `scene_version` | yes | The Scene version written, `"4.3"` (`"4.0"` to `"4.2"` output still loads); a different major or a newer minor does not load |
| `extensions` | no | File name endings the decoder opens, lowercase with the dot, such as `.acme.jsonl`. Without any, it is picked only by id |
| `bases` | no | `["frame"]`, `["time"]` or both: what a lane may match your records to video frames by. Frame: record k belongs to frame k (or the frame number you key by); a record is a `skipped` line or the samples in a row with one start key. Time: by the records' timestamps. Default `["frame"]` |
| `default_basis` | no | One of `bases`, the one a lane starts with; default the first listed |
| `options` | no | A list of fields, below, of kind `text`, `file` or `folder` only. Values are always text |

Task handler (`"kind": "task"`): `id`, `name`, optional `description`, `lanes` and `video` as under [Lanes](#lanes), and `options`
as a decoder's. Its output has no Scenes: `{"progress":{"done"?,"total"?,"text"?}}`, `{"log":{"text"}}`, `warning`, `error` and
`{"done":{}}`; the exit code decides the end, and the user dropping the run kills the program. A task writes where
its own `folder` option or setting says.

Playlist resolver handler (`"kind": "playlist_resolver"`): an `id` other than `help`, `description` (one sentence, required) and `settings`, an
optional list of fields, below. The settings become a form in the app and arguments of `ax-devil playlist ID`.
`watches` (optional, default `false`): the plugin answers the `watch` operation ([below](#output-of-scan-and-resolve)).

A field is `{"key","label","kind","required","default","positional", ...}`. `key` is letters, digits, `_` or `-`,
starts with a letter or digit, and in a resolver is not `help`, `config`, `cache-dir` or `log-level`. It is the flag
`--<key>` (`_` as `-`) unless `positional` (which needs `required`, no `default`, and a single value).
`required` defaults to false. `default` is text, or a whole number for an integer. `kind` and what it adds:

| Kind | Value | Adds |
|------|-------|------|
| `text`, `folder` | text | |
| `file` | text | `extensions`: lowercase with the dot |
| `integer` | whole number | `min` (default 0) |
| `handler` | one decoder id | |
| `decoder_options` | a map of that decoder's options | `handler_field`: the key of the `handler` field it belongs to |
| `scan` | a list of values the plugin's `scan` found; left out means all | `depends_on` (optional): keys of the other settings the scan reads, not a `scan` or `decoder_options`; `count` (optional): `{"missing","found","none"}` sentences, `found` containing `{count}`, which make the scan only report how many values it found: the setting then holds no value; at most one scan per resolver has it |

The cache of a file's samples is keyed by the handler id, the SHA-256 of the manifest, the plugin's settings and its `code` files,
the declared options (defaults filled in; undeclared keys are dropped with a warning). Editing a code file reruns the
plugin the next time the file opens, and a lane already open keeps what it read.

## Registry `$XDG_DATA_HOME/ax-devil/plugins.json`

`{"version": 2, "plugins": [{"manifest": "/absolute/path/ax-devil-plugin.json"}]}` (default
`~/.local/share/ax-devil/plugins.json`). `plugins install`, `update` and `remove` write it atomically; a plugin is
never found by scanning folders. A running app rereads it when it or a listed manifest changes. A broken registry
loads no plugin; a broken entry fails alone. Both are listed by `plugins list`.

## Running

The host starts `command` in an empty private working folder (also `TMPDIR`), removed at the end, so rely on the
absolute paths in the request. The environment holds only `PATH`, `HOME`, `LANG`,
`LC_ALL`, `LC_CTYPE`, `TZ` (on Windows also `SYSTEMROOT`) and the manifest's `env`. A `path` decoder's stdin gets one
line, then EOF:

```json
{"protocol":3,"op":"decode","handler":"acme_tracks","input":{"path":"/abs/overlay.file"},"options":{"camera":"front"}}
```

`handler` is the id of the decoder called, so one program can serve several. `options` holds every declared option
that has a value. The request says nothing of the video, so a file in pixels needs its image size as options. Read
stdin, then the file at `input.path`; write to stdout. (A `records` decoder is read differently, below.)
Stderr is drained: its last 20 lines (2 KiB) join a failure's reason, and it is logged at DEBUG only.

### Lanes

A `lanes` decoder and a task declare `"lanes": [{"key","label"}]` (the lanes the user picks, read under `key`) and
`"video": true` to also get the entry's video. The request carries files, never a bundle:

```json
{"protocol":3,"op":"decode","handler":"acme_iou","input":{"lanes":{"truth":"/abs/a.jsonl","model":"/abs/b.jsonl"},"video":"/abs/v.mp4"},"options":{},"settings":{}}
```

(`"op":"task"` for a task.) A lane file is JSON Lines: `{"lane":{"basis","coverage","metadata"}}`, then one `{"sample": Scene}`
per stored sample, keyed `{"frame":N}` or `{"us":N}`. A decoder prints Scenes as usual, and a lane that changes any
input is recomputed. `fixtures/plugins/derived-demo` is the model.

### Records

A decoder with `"input": "records"` gets a request without a path, then one line per record, and EOF when the records
end:

```json
{"protocol":3,"op":"decode","handler":"acme_live","input":"records","options":{"camera":"front"}}
{"record":{"text":"{\"speed\":42}"}}
{"record":{"base64":"/wAB"}}
```

A record is `text`, trimmed, when its bytes are UTF-8 and `base64` otherwise; text that is blank is no record. For a
file a record is a line; for a live source it is one document, such as one message. The host sends a record only after the
plugin has answered the one before: print zero or more output lines for it, then `{"done":{}}`. No sample is "no
overlay for this record"; `{"skipped": "why"}` drops it (counted, and reported at the record's position, not at the
output line's number). `done` is not accepted in a `path` decoder's output.

- One process serves a live source for as long as it stays connected (a reconnect starts a new one, so state the plugin
  keeps starts over), or a file to its end. It is started with the first record and exits 0 at EOF. The `header`, if any, comes once per
  process, before its first sample; the host keeps the first. Live samples are keyed by time (`{"us": N}`).
- `idle_timeout_s` counts only while an answer is owed: a plugin may stay silent between records for as long as it likes.
- A process that ends (exits, prints `error`, goes idle, breaks the protocol) after it answered a record costs the
  record it was working on, which is skipped, and starts again with the next one. One that ends before its own first answer (a restarted one too)
  fails the session with the reason, so a plugin that cannot run is not started once per record.

Scan and resolve send the settings the user has entered, checked against the form with defaults filled in, and no
overlay path:

```json
{"protocol":3,"op":"scan","handler":"acme_runs","field":"runs","settings":{"root":"/abs/runs"}}
{"protocol":3,"op":"resolve","handler":"acme_runs","settings":{"root":"/abs/runs","runs":["a","b"]}}
```

## Output: JSON Lines on stdout

UTF-8, one JSON object per line, `\n` or `\r\n`; blank lines are ignored; a line may be up to 16 MiB. Each line has
exactly one key:

| Line | Meaning |
|------|---------|
| `{"header": {...}}` | At most once, before the first sample. `coverage`: `"dense"` (a frame without a sample is empty and sticky never crosses it) or `"sparse"` (default; nothing is said about it). `filter`: classification options `{"id","label","default" (true),"types" [],"include_empty" (false),"exclude" (false)}`. `metadata`: an object of what the file says about itself |
| `{"sample": Scene}` | One Scene. Samples with the same start key are joined: entities by id (observations added in order), events, relations and debug appended |
| `{"replace": Scene}` | Like `sample`, but drops what its start key already held |
| `{"warning": {"problem","reasons":[],"consequence"}}` | A problem the user should see; texts cut to 200 characters, three reasons |
| `{"skipped": "reason"}` | A record you could not convert; counted |
| `{"error": {"reason","field"?,"path"?}}` | The run cannot go on. Ends it at once as Incomplete with `reason` (`field` and `path` are for other operations and ignored here); later lines are not read |
| `{"done": {}}` | `records` decoders only: the answer to the record is complete |

A line that is not valid JSON, has none or several of these keys, or holds a Scene that breaks a model rule is skipped
and counted by line number. At the end one warning gives "Skipped N of M records" with up to three reasons. A header
that comes late or twice ends the run as a protocol error.

The run ends **Complete** when the plugin exits 0 after its last line. Any other end is **Incomplete**: an `error` line, a nonzero
exit or signal, nothing printed for `idle_timeout_s`, a line over 16 MiB, a protocol error, an unreadable pipe. The
samples already read stay and the lane shows the reason; if none arrived, the overlay fails to open. On every end,
and when the lane is closed, the host kills the plugin and (not on Windows) everything it started.

## Output of scan and resolve

Same framing and limits; each line has exactly one key. `warning` and `error` are as above, but a scan or resolve
stops at an `error` and fails with its `reason`, about the setting `field` or the folder or file `path` when given,
which a form shows at that setting.

| Line | Op | Meaning |
|------|----|---------|
| `{"choice": {"value","label"?,"detail"?}}` | scan | One value of the `field`; `label` defaults to `value` |
| `{"playlist": {"name","matches"?,"sequence_groups"?,"metadata"?}}` | resolve | Starts a playlist; the entries after it belong to it. May come any number of times, with the same names in the same order on every run. Entries before the first one form a playlist named like the resolver. A shared playlist name, and a shared entry name within a playlist, gets ` (2)`, ` (3)`. `matches` and `sequence_groups` are whole numbers up to 2^53-1 shown in Information, and so is `metadata`, a JSON object of free-form facts, read as the entry line says |
| `{"entry": {"name","video","overlays"?,"considered"?,"metadata"?}}` | resolve | One video. `video` is `{"file": "/abs/v.mp4"}` or `{"image_sequence": {"pattern": "/abs/img1/%06d.jpg","start_number","frame_rate": {"num","den"},"width","height","frame_count"}}`. An overlay is `{"label","slot","path","handler","options"?,"considered"?,"metadata"?}`: `slot` is the lane's identity, the same in every entry that has that lane; `handler` is a decoder id. `considered` (default `true`) `false` starts the entry or overlay hidden until the user shows it. `metadata` (a playlist's, an entry's or an overlay's) is shown in Information, sorted by key: `true` and `false` as Yes and No, decimals with at most three digits, a list joined by commas (`(empty)` when empty), `{}` for an empty object. A metadata object may take 16 KiB of JSON and all of a resolve's 2 MiB, or the run fails; Information shows 32 rows of at most 200 characters, the last of them "N more" when there are more keys |
| `{"watch": "/abs/file"}` | watch | A file, besides the settings' folders and files and the overlays, whose change in place makes the playlist stale, such as a descriptor the entries are read from. Need not exist yet; at most 50,000 files and 2 MiB of path text. Only `watch`, `warning` and `error` lines are allowed |

The request `{"protocol":3,"op":"watch","handler":"acme_runs","settings":{...}}` is sent before every resolve, with the
resolve's settings, and only to a handler whose manifest says `watches`. It runs while the item is opened, so keep it
fast. A failed `watch` (an `error`, a nonzero exit, `idle_timeout_s`) is logged and the resolve goes on without it; its `warning` lines are logged too and not shown to the user.

The host fails the operation, naming the line, when a line is not valid JSON or has the wrong keys, a path is not
absolute, a playlist name is empty, or a slot or label repeats within an entry. A playlist with no entry, or no
playlist at all, fails the resolve. Nothing is used unless the plugin then exits 0; a failed start, a nonzero exit or no output for `idle_timeout_s` fails with the reason and the end of stderr.
A scan or resolve keeps the first 64 warnings and summarizes the rest in one; one that prints more than 50,000 choices or 50,000 entries and playlists is stopped and fails, naming the limit.
A scan or resolve the user abandons kills the plugin.

## Scene

A sample is one Scene in the JSON of `scene::serial`. Times are `{"frame": N}` (frame-keyed) or `{"us": N}`
(microseconds on the video's clock); one file uses one kind. An observation's `time` carries either clock, independently
of the Scene key, or both, `{"frame": N, "us": N}`. Frame numbers are integers; a Scene keyed before frame 0 is skipped
with a warning. Geometry is normalized to `[0,1]` of the frame. Absent
parts may be left out. Within one Scene, observations follow Python's arrival rule: each arrival moves before strictly
greater neighbours on its timestamp clock, if supplied, otherwise its frame clock; a missing clock or equal/smaller
value stops the walk. Same-key `sample` Scenes are each ordered first, then their ordered lists are replayed in receive
order when joined.

```json
{"time": {"start": {"us": 100000}, "end": {"us": 100000}},
 "entities": [{"id": "7", "end_reason": null, "motion_state": "moving",
   "observations": [{"time": {"us": 100000},
     "geometry": {"box": [0.1, 0.2, 0.3, 0.4]},
     "classifications": [{"type": "car", "score": 0.9, "confidence": null, "attributes": {}}],
     "measurements": {"image_velocity": {"vx": 0.01, "vy": 0.0}},
     "source_data": {}, "debug": {}}]}],
 "events": [{"time": {"start": {"us": 100000}, "end": {"us": 100000}},
             "operation": {"rename": {"from": "6", "to": "7"}}}],
 "relations": [{"type": "follows", "source": "7", "target": "6"}]}
```

- The Scene's key is `time.start`. Emit Scenes as the file gives them, one per object or per frame; the host joins equal keys.
- Geometry is `{"box": [left, top, right, bottom]}` or `{"polygon": [[x, y], ...]}`; custom shapes require Scene 4.2 and use
  `{"shape": {"bounds": [left, top, right, bottom], "outline": [[x, y], ...]}}` for custom geometry, with
  `outline: null` when absent. Coordinates are normalized, finite and may extend outside the frame; bounds must
  be ordered and an outline needs at least three points. Custom bounds are independent of outline extrema.
- `type` is any string; `score` and `confidence` are separate finite numbers or `null`. Never derive one from the other.
- `motion_state`: `moving`, `stationary`, `unknown` or `null`. `end_reason`: text or `null`.
- `measurements` (all optional): `image_velocity {vx, vy}`, `world_velocity {pitch, speed, yaw}`,
  `geographic {latitude, longitude, elevation?}`, `world {distance, azimuth, elevation}`.
- `events` operations: `delete {entity}`, `rename {from, to}`, `merge {sources, target}`, `split {source, targets}`.
- `source_data` holds what the file said verbatim; `debug` what the plugin computed. Both hold only null, bool,
  number, string, list and map.
- A classification attribute that is a list of `{"rgb": [r, g, b], "name"?, "score"?}` is a colour list a catalog can
  colour by; bool, number and string attributes are also shown.
- `entities[].images` (`{"timestamp_us", "box", "data": base64}`) carries crops.

## What the user sees while it runs

The lane fills as samples arrive, but only when a header came first and keys ascend. Without a header, or after a
key below an earlier one, frames wait for the end of the run, where the host sorts and joins them. Reopening an
unchanged file reads the stored result and does not run the plugin.
