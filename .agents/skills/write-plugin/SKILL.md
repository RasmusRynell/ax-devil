---
name: write-plugin
description: Make ax-devil read overlay data in a format it does not read, by writing, checking and installing a converter (a program in any language that prints Scenes as JSON Lines). Use when the user wants their own annotation, tracking or detection files drawn on a video, or a change to such a converter.
---

# Write a converter

A converter is a program in the user's own folder, outside this repository. ax-devil runs it on an overlay file, reads
Scenes from its stdout and draws them like any built-in format. A decoder shipped with ax-devil is a built-in in
`crates/scene-decoders` and is development work; playlist resolvers are built in only. The contract is in
[reference.md](reference.md); `docs/plugins.md` says what installing one means for the user's safety.

## Steps

1. Run `ax-devil list-handlers`. MOT, UVG-VCM, ONVIF XML and CVAT need no converter.
2. Read a real file. Decide what keys a sample (a frame number or a time in microseconds, one kind per file), whether
   every frame has a sample (`dense`) or only some (`sparse`), what the object ids and classes are, and what is in the
   file besides geometry.
3. Create a folder with `ax-devil-converter.json` and the program. Start from `fixtures/converters/python-tracks`
   (Python, every Scene part) or `fixtures/converters/shell-times` (sh and awk, time keys, `replace`).
4. Install it and prove it on the user's real file, not only a toy one:

   ```bash
   ax-devil plugins install FOLDER
   ax-devil plugins check ID FILE --frame-size 1920x1080 [--option KEY=VALUE]
   ```

   `check` prints the sample count, the key kind and range, the warnings and why a run stopped early. It exits 1
   when the run is incomplete or empty. Give the real size of the video's frames: geometry is divided by it.
5. Open the video with the file (`ax-devil local --video V --overlay FILE`; the extension picks the converter, or
   `--handler-type ID`). Compare the boxes, labels and the inspector's fields with what the file says. To see it without the
   user's desktop, follow [verify-headless](../verify-headless/SKILL.md).

After an edit, run `check` again: it starts a new process and sees the manifest as well as the code. A running app
rereads code on the next open and the manifest only at start.

## Rules

- stdout is the protocol and nothing else: print diagnostics to stderr, flush after every line.
- Normalize geometry to `[0,1]` with the request's frame size. Do not derive or invent values: a score, a
  confidence or a class the file lacks stays `null`. Keep the file's raw fields in `source_data`, what you computed in
  `debug`.
- A record you cannot convert is `{"skipped": "why"}`, not a crash and not a made-up Scene. A problem that affects
  the whole file is a `warning`.
- Print the `header` first (coverage, class filters) and keys in ascending order, so the lane fills while the
  converter runs. Out-of-order keys are accepted but the lane waits for the end.
- Read only the overlay path you were given. Write nothing outside the working folder, make no network calls, and
  take nothing from the environment beyond the names the manifest lists in `env`.
- Ids are stable text per object across samples; the same id in two samples of one key is one object.
