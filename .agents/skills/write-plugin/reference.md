# Plugin reference

The one specification of the plugin contract (protocol 3). Code that enforces it: `crates/plugins`. The model plugin
is `fixtures/plugins/python-tracks`; a second, in POSIX shell and awk, is `fixtures/plugins/shell-times`. The model
resolver is `fixtures/plugins/python-runs`.

A plugin is a program and a manifest that lists the **handlers** it provides, of kind `decoder` (an overlay file in,
Scenes out) or `playlist_resolver` (settings in, playlists out). One handler call is one run of the program.

## Manifest `ax-devil-plugin.json`

A JSON object in the plugin's folder. Unknown fields, a wrong type or a missing required field refuse it, naming the
field by its path, such as `handlers[1].options[0].kind`.

| Field | Required | Meaning |
|-------|----------|---------|
| `manifest` | yes | `2` |
| `id` | yes | The plugin id: 1-64 characters of `a-z 0-9 . _ -`, starting with a letter or digit. Must differ from every built-in and installed plugin id |
| `name`, `version` | yes | Shown to the user; `version` is free text |
| `protocol` | yes | `3`; any other value does not load |
| `command` | yes | Program and arguments. A program containing `/` is relative to the folder and must be executable; otherwise it is looked up on `PATH`. Later items that name a file or folder of the plugin become absolute; items starting with `-` are passed as written. Nothing is appended: the request carries the path |
| `env` | no | Names of environment variables to pass through besides the standard few. `AX_DEVIL_*` is refused |
| `code` | no | Paths in the folder (files or folders, no `..`) whose bytes identify the plugin; default the whole folder. Dot-names and `__pycache__` are skipped; at most 20,000 files and 256 MiB |
| `idle_timeout_s` | no | 1 to 86,400; default 120. Seconds the plugin may print nothing before it is killed |
| `handlers` | yes | A non-empty list of handlers, below |

Every handler has `kind` and `id` (same characters as the plugin id; it may equal the plugin's own id, and must
differ from every other handler id, built-in or installed, of any kind; `--handler-type` takes it) and `name`. A
`kind` this ax-devil does not know is skipped, unread and without an id, and listed under the plugin as needing
a newer ax-devil; the plugin's other handlers work, and a plugin left with none fails.

Decoder handler (`"kind": "decoder"`):

| Field | Required | Meaning |
|-------|----------|---------|
| `input` | yes | `"path"`: the request carries the overlay file's path |
| `scene_version` | yes | The Scene version written, `"4.0"`; a different major or a newer minor does not load |
| `extensions` | no | File name endings the decoder opens, lowercase with the dot, such as `.acme.jsonl`. Without any, it is picked only by id |
| `bases` | no | `["frame"]`, `["time"]` or both: what a lane may match your records to video frames by. Frame: record k belongs to frame k (or the frame number you key by); a record is a `skipped` line or the samples in a row with one start key. Time: by the records' timestamps. Default `["frame"]` |
| `default_basis` | no | One of `bases`, the one a lane starts with; default the first listed |
| `options` | no | A list of fields, below, of kind `text`, `file` or `folder` only. Values are always text |

Playlist resolver handler (`"kind": "playlist_resolver"`): an `id` other than `help`, `description` (one sentence, required) and `settings`, an
optional list of fields, below. The settings become a form in the app and arguments of `ax-devil playlist ID`.

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
| `scan` | a list of values the plugin's `scan` found; left out means all | `depends_on` (optional): keys of the other settings the scan reads, not a `scan` or `decoder_options` |

The cache of a file's samples is keyed by the handler id, the SHA-256 of the manifest and the plugin's `code` files,
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
`LC_ALL`, `LC_CTYPE`, `TZ` (on Windows also `SYSTEMROOT`) and the manifest's `env`. Stdin gets one line, then EOF:

```json
{"protocol":3,"op":"decode","handler":"acme_tracks","input":{"path":"/abs/overlay.file"},"options":{"camera":"front"}}
```

`handler` is the id of the decoder called, so one program can serve several. `options` holds every declared option
that has a value. The request says nothing of the video, so a file in pixels needs its image size as options. Read
stdin, then the file at `input.path`; write to stdout.
Stderr is drained: its last 20 lines (2 KiB) join a failure's reason, and it is logged at DEBUG only.

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
| `{"playlist": {"name"}}` | resolve | Starts a playlist; the entries after it belong to it. May come any number of times, with the same names in the same order on every run. Entries before the first one form a playlist named like the resolver. A shared name gets ` (2)`, ` (3)` |
| `{"entry": {"name","video","overlays"?}}` | resolve | One video. `video` is `{"file": "/abs/v.mp4"}` or `{"image_sequence": {"pattern": "/abs/img1/%06d.jpg","start_number","frame_rate": {"num","den"},"width","height","frame_count"}}`. An overlay is `{"label","slot","path","handler","options"?}`: `slot` is the lane's identity, the same in every entry that has that lane; `handler` is a decoder id |

The host fails the operation, naming the line, when a line is not valid JSON or has the wrong keys, a path is not
absolute, a playlist name is empty, an entry name repeats within its playlist, or a slot or label repeats within an entry. A playlist with no entry, or no playlist at all, fails the resolve with "<resolver name> has nothing to open." Nothing is used unless the plugin then
exits 0; a failed start, a nonzero exit or no output for `idle_timeout_s` fails with the reason and the end of stderr.
A scan or resolve keeps the first 64 warnings and summarizes the rest in one; one that prints more than 50,000 choices or 50,000 entries and playlists is stopped and fails, naming the limit.
A scan or resolve the user abandons kills the plugin.

## Scene

A sample is one Scene in the JSON of `scene::serial`. Times are `{"frame": N}` (frame-keyed) or `{"us": N}`
(microseconds on the video's clock); one file uses one kind. Geometry is normalized to `[0,1]` of the frame. Absent
parts may be left out.

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
- Geometry is `{"box": [left, top, right, bottom]}` or `{"polygon": [[x, y], ...]}`.
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
