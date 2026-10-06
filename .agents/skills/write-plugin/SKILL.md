---
name: write-plugin
description: Write an ax-devil plugin for the user's own data — a decoder that reads their annotation files, model output (JSON, CSV, XML, protobuf...) or live messages into overlays, or a playlist resolver that pairs their folders, manifests or experiment runs with videos. Use whenever the user wants ax-devil to open a format it does not support yet, or to load their datasets in one step, including new built-in plugins.
---

# Writing a plugin for the user's data

The user has data ax-devil cannot open yet. Your job: understand their data, write a small plugin package outside
this repository, install it, prove it decodes their real files, and tell them the one command that opens it.

## Pick the plugin type

| The user wants to... | Plugin | Built-in model to imitate |
|---|---|---|
| Open an overlay file next to a video | Decoder with a **file** handler | `src/ax_devil/plugins/decoders/mot/` (CSV), `uvg_vcm/` (JSON), `cvat/` (XML) |
| Decode live messages (RTSP metadata, MQTT, DataHub) | Decoder with a **payload** handler | `src/ax_devil/plugins/decoders/adf_v1/`, `onvif_xml/` |
| Open many recordings with their overlays at once, or compare runs | **Playlist resolver** | `src/ax_devil/plugins/playlist_resolvers/folder_pair/`, `mot_challenge/` |

First check that no built-in handler already reads the data: `uv run ax-devil list-handlers`. A dataset of a
supported format only needs a resolver; a new format with a folder layout may need both, in one package.

## The loop

1. **Look at real data.** Ask for a sample file or folder if the user has not given one, and read it. Find: how
   frames are identified (frame number, timestamp), how geometry is expressed (pixels or normalized, xywh or corners,
   polygons), object ids, classes, scores, and any extra fields worth keeping as attributes. Ask about anything you
   cannot infer, such as the frame size for pixel coordinates.
2. **Read the contract.** [reference.md](reference.md) has a minimal package and the packaging rules. Read the
   built-in model from the table above end to end, and `src/ax_devil/modules/scene/model.py` for the Scene types
   (`Entity`, `Observation`, `BoundingBox`, `Classification`, `Attribute`).
3. **Create the package outside this repository**, in a directory the user picks (default
   `~/ax-devil-plugins/<name>/`): `pyproject.toml` with the entry point plus one module. Keep the user's data
   untouched and out of the package. A plugin meant to ship with ax-devil is development instead: it goes in
   `src/ax_devil/plugins/` next to its model, with tests, following `AGENTS.md`, and needs no install step.
4. **Map their data to Scenes.** Normalize geometry to 0–1 of the frame. Keep stable object ids so tracking,
   history and ID-colored catalogs work. Put extra fields in `Attribute`s on the classification so the inspector
   shows them and render catalogs can use them. Set `file_extensions` on file handlers.
5. **Prove it on their data before installing.** Run a short script with `uv run python` from this repository, with
   the package directory on `PYTHONPATH`, that decodes the user's sample and prints the frame count, objects on a
   few frames and one decoded object with its attributes. Compare against the raw file. A resolver prints its
   resolved recordings and overlay pairings instead.
6. **Install it with the command the user launches ax-devil with:** `ax-devil` for an installed app,
   `uv run ax-devil` for this checkout. Each keeps its own plugins, so ask if unclear. Run
   `<launch command> plugins install /absolute/path/to/package`; installation validates the entry point. Confirm with
   `plugins list`, and for file handlers `list-handlers`.
7. **Hand over.** Give the user the exact command that opens their data with their launch command, for example
   `ax-devil local --video <video> --overlay <file> --handler-type <HANDLER>` or
   `ax-devil playlist <resolver_id> ...`. Launching the app is theirs to do. Report what each field of their
   data became (geometry, id, class, attributes), and anything you skipped and why.

The package is installed editable: source edits appear on the next app start. After changing dependencies or entry
points, run `plugins update` with the same launch command. Upgrades, removal and recovery are in `docs/plugins.md`.

## Writing it well

- Match the built-in model's structure; keep the plugin to the few files the model uses.
- Fail loudly on a file that is not the expected format; skip and log single malformed records.
- To change how the decoded objects look, follow the `render-catalog` skill; the plugin only supplies the data.
