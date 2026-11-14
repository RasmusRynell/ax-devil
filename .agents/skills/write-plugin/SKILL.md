---
name: write-plugin
description: Make ax-devil read overlay data in a format it does not read, by writing, checking and installing a plugin (a program in any language whose decoder handler prints Scenes, or whose playlist resolver handler prints a playlist, as JSON Lines; also a lane computed from other lanes, a task, or a page shown in the app). Use when the user wants their own annotation, tracking or detection files drawn on a video, results derived from lanes, or a change to such a plugin.
---

# Write a plugin

A plugin is a program in the user's own folder, outside this repository, with a manifest listing the handlers it
provides. For an overlay format that is a decoder handler: ax-devil runs the program on an overlay file, reads Scenes
from its stdout and draws them like any built-in format. For a dataset layout it does not know that is a playlist
resolver handler: the program turns settings (a folder, choices it scans for) into videos with their overlay files.
A handler shipped with ax-devil is a built-in in `crates/handlers` (`decoders` or `resolvers`) and is
development work. The contract is in
[reference.md](reference.md); `docs/plugins.md` says what installing one means for the user's safety.

## Steps

1. Run `ax-devil list-handlers`. MOT, UVG-VCM, ONVIF XML and CVAT need no plugin.
2. Read a real file. Decide what keys a sample (a frame number or a time in microseconds, one kind per file), whether
   every frame has a sample (`dense`) or only some (`sparse`), what the object ids and classes are, and what is in the
   file besides geometry.
3. Create a folder with `ax-devil-plugin.json` and the program. Start from `fixtures/plugins/python-tracks`
   (Python, every Scene part) or `fixtures/plugins/shell-times` (sh and awk, time keys, `replace`); a resolver from
   `fixtures/plugins/python-runs`. A resolver prints absolute paths and names a decoder id for each overlay.
4. Install it and prove it on the user's real file, not only a toy one:

   ```bash
   ax-devil plugins install FOLDER
   ax-devil plugins check HANDLER FILE [--option KEY=VALUE]
   ```

   `check` prints the sample count, the key kind and range, the warnings and why a run stopped early. It exits 1
   when the run is incomplete or empty. The plugin never sees the video: a file in pixels needs its image size as options (`width`, `height`).
5. Open the video with the file (`ax-devil local --video V --overlay FILE`; the extension picks the decoder, or
   `--handler-type HANDLER`). Compare the boxes, labels and the inspector's fields with what the file says. To see it without the
   user's desktop, follow [verify-headless](../verify-headless/SKILL.md).

A decoder for a live source (a camera's metadata documents) declares `"input": "records"` and `time` in `bases`: it
answers one record at a time, which `fixtures/plugins/shell-records` shows. `check` reads such a decoder's file line by line.

A decoder with `"input": "lanes"` computes a lane from other lanes of the entry, and a `task` reads lanes and writes
where its own options say (`fixtures/plugins/derived-demo`; [reference](reference.md#lanes)). `check` reads one file, so
run these by hand: pipe a request with lane files to the program, such as
`echo '{"protocol":3,"op":"decode","handler":"ID","input":{"lanes":{"KEY":"a.jsonl"}},"options":{},"settings":{}}' | ./main.py`,
then add the lane from the app. A page shown in the app, or a menu entry that runs a handler, is a view or command:
[views.md](views.md) holds the bus contract and a helper to copy.

A resolver has no `check`: `ax-devil playlist ID --help` shows its settings, and `ax-devil playlist ID ARGS` opens the
playlist; a wrong path or name fails with the line it came from.

After an edit, run `check` again: it starts a new process and sees the manifest as well as the code. A running app
rereads code on the next open and the manifest within a second or two.

## Rules

- stdout is the protocol and nothing else: print diagnostics to stderr, flush after every line.
- Normalize geometry to `[0,1]` with the image size the file states or an option gives; the plugin never sees the video. Do not derive or invent values: a score, a
  confidence or a class the file lacks stays `null`. Keep the file's raw fields in `source_data`, what you computed in
  `debug`.
- A record you cannot convert is `{"skipped": "why"}`, not a crash and not a made-up Scene. A problem that affects
  the whole file is a `warning`.
- Print the `header` first (coverage, class filters) and keys in ascending order, so the lane fills while the
  plugin runs. Out-of-order keys are accepted but the lane waits for the end.
- Read only the overlay path you were given. Write nothing outside the working folder, make no network calls, and
  take nothing from the environment beyond the names the manifest lists in `env`.
- Ids are stable text per object across samples; the same id in two samples of one key is one object.
