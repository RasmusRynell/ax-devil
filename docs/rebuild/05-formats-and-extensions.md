# 05 Formats and Extensions

Owns: file formats of the new app, versioning, atomic writes, extension points and the plugin contract. What to carry
over by hand from the old tree is in [migration.md](migration.md).

## Format policy

The new app defines its own formats ([README](README.md#delivery)). One format is kept deliberately:

- **Render catalog language, schema v4** (D25), defined in a spec to be written; it replaces v3
  (`scene/rendering/catalog_definitions/`). Reason: the frontend compiles and evaluates catalogs per lane (D22), so
  styles, frame filters and draw order share one language. The language is the main agent-facing extension surface;
  the six built-ins are rewritten as complete v4 documents, and the JSON Schema and reference are generated from the
  TypeScript definitions ([10](10-quality-gates.md#gates-must)).

Rules for every format the new app owns (MUST):

- Structured application documents (workspace, recovery copy, recents, config, catalogs) and cache and wire
  envelopes carry an explicit version with one current shape; an unsupported version is rejected with a user-facing
  message. Scene and plugin contracts follow their own major/minor policy (below). Standard formats (CSV, video
  containers, plain-text logs) carry no version field.
- Completed artifacts and snapshots are replaced atomically: temp sibling, replace on success, old file kept on
  failure or cancel (*current:* `src/ax_devil/core/atomic_file.py`). Logs append and rotate; lock files are created
  exclusively and removed by their owner.
- Never write packaged built-in resources.

## Formats the new app needs

Use the current definitions as design references, not compatibility targets.

| Format | Design reference | Rules to keep |
|--------|------------------|---------------|
| Workspace file (JSON, `.ax-devil.workspace`) | `docs/architecture/workspace.md#file-format` | Items are recipes; unknown items preserved and saved back; relative paths inside the file's folder, lexically normalized; credentials as entered; new files private |
| Recovery copy and lock | `docs/architecture/workspace.md#lifecycle` | Exists exactly while unsaved edits do; names the file it recovers; one instance owns it |
| Recent workspaces | `workspace/core/recent_workspaces.py` | Bounded list of path and last-used time; listing never reads the files |
| Config | `settings/config_manager.py` (`DEFAULT_CONFIG`), `docs/settings.md` | Shape follows the settings model ([08](08-settings-cli.md#settings-model)), shortcut overrides included; unknown keys preserved and logged |
| Window state | `settings/window_state.py` | Not a versioned document: size and maximized state only, kept apart from config; unreadable state falls back to defaults |
| Render catalogs | above | Every JSON file in the user folder is a catalog; default choice stored beside them |
| Bookmarks CSV | `workspace/core/bookmark_csv.py` | Header row; one row per bookmark |
| Export video | `video_viewer/export/encoder.py`, `docs/domain/invariants.md#export` | Source frame timing kept; no audio; quality presets (format and encoder: D6, decided when export is built; default MP4/H.264 through the linked FFmpeg) |
| Scene serialization | new, below | — |
| Caches | `docs/domain/invariants.md#data-pipeline` | Full-identity invalidation; private; rebuildable |
| Logs | `settings/logging_config.py` | Rotating plain text ([03](03-runtime.md#logging-must)) |

## Scene serialization

- **MUST** define one versioned Scene serialization schema in M2. It serves as the plugin wire format, the golden
  fixture format ([09](09-testing.md#oracle-policy)) and the cache artifact payload.
- **MUST** follow the Scene version rules in `docs/domain/invariants.md#scene-model`. A decoder loads when its major
  equals the host's and its minor is not newer (`plugin_system/decoder_plugins.py`, `validate_registration`). The
  plugin API version is a separate check (`plugin_system/base.py`). The host normalizes accepted older-minor output to
  the current schema before caching; cache identity includes the host schema version.
- **MUST** restrict free-form fields (`Attribute.value`, `source_data`, `debug`) to schema value types (null, bool,
  number, string, list, map). *Current:* any picklable Python object. A colour list (name, colour, score per item) is
  one more typed value (D26), so catalogs can colour by attribute from a Sample Item.

## Decoder contract

Sources: `.agents/skills/write-plugin/SKILL.md`, `.agents/skills/write-plugin/reference.md`, `plugin_system/`,
`scene/decoding/`.

- File handlers (MUST): declared extensions, unique handler type ids per capability, whole-file warnings and
  optional classification filter policies. Independent malformed records may be skipped with a lane warning while
  preserving source frame/time keys. A malformed document or frame whose omission would hide invalid ground truth
  fails the load (UVG-VCM); an explicit empty Scene remains a successful sample, distinct from absent data.
- Declared metadata the host-owned store needs (*current:* `SceneDecoderFileProvider.__init__` in
  `data_sources/file_data_provider/scene_decoder_file_provider.py`): storage mode (source index decoded on demand, or
  derived cache for whole-file parsing), independent records (enables incremental re-decode), sequence lookup
  support, timestamp fallback policy, `artifact_version`, and decode options, which are part of cache identity.
- Line-record handlers decode each record independently of earlier calls, because records are decoded in seek order
  by one reused instance (`plugin_system/line_decoder_plugin.py`).
- Payload handlers may be stateful (`scene/decoding/payload_to_scene_decoder.py`; the built-in ones are not). Payload
  decode is lossless and its queue overflow is a reported lane error ([03](03-runtime.md#real-time-delivery-must)).
  The host resets a decoder on transport reconnect and helper restart.
- Decoders yield per-frame Scenes or records; the host builds the indexed store ([02](02-architecture.md#seams-to-redraw)),
  grouping equal-timestamp records and sorting at the end of ingest.

## Resolver contract

Source: `docs/architecture/workspace.md#playlist-resolver-contract`. **MUST** keep headless `resolve(settings, token)`,
JSON settings saved opaquely, optional source paths to watch, user-facing errors. **change** (D4): the settings form
and CLI arguments are generated from a declared schema that supports asynchronous, cancellable dependent choices
(sequences found by scanning a folder), multi-selection, choices filled from the registry (decoders), "all" distinct
from an explicit list, and preserving unknown settings and unavailable saved selections on edit. *Current:*
`src/ax_devil/plugins/playlist_resolvers/mot_challenge/settings_widget.py` and `src/ax_devil/plugins/playlist_resolvers/folder_pair/settings_widget.py`. The D0 spike
validates the schema against both.

## Plugin runtime

Guarantees any mechanism must meet (`docs/domain/invariants.md#plugin-system`, `docs/plugins.md`):

- Discovery only through a declared registry; never by folder presence.
- API and Scene version checks before registration; failures listed, never fatal.
- Plugin dependencies isolated from the app's; installing a plugin never changes the app.
- Install, update, remove from the CLI and the running app; validated before activation; applied on restart.
- Authoring stays cheap for an agent: one small package, a local install command, edits picked up on restart.

"Python stays an authoring language" differs from "existing plugins run unchanged" (PL-05). Existing plugins also use
hooks a converter process does not carry:

| Hook | Current | Porting need |
|------|---------|--------------|
| Resolver settings widget and restore | `create_settings_widget`, `restore_settings` (Qt) | Replaced by the settings schema (D4) |
| Resolver CLI command | `create_cli_command` (Click) | Generated from the schema |
| Filter factories and predicates | `filter_factory`, `FilterOption` predicates in `filtering/predicate_utils.py` | Declarative classification policies only, or evaluated in the plugin process |
| File provider factories | `FileToSceneDecoderDefinition.factory`, `create_provider` | Host-owned store fed by the decoder stream |
| Resolver file watching | `source_paths` | Keep, as data |
| Arbitrary values in Scenes | `Attribute.value`, `source_data`, `debug` | Schema value types only |

D1 is settled by D29: built-in decoders and resolvers are native; third-party authors port their plugins to the new
contract; unchanged plugins are not supported (PL-05); there is no in-process Python bridge. Other data enters through
converter processes (any language, JSON Lines):

- A manifest declares the converter; it runs with an allowlisted environment, its stderr is drained, a line over
  16 MiB fails it, and a kill takes its whole process group.
- A failure marks the lane incomplete; cache identity includes the script hash
  (`docs/domain/invariants.md#data-pipeline`).

## Other extension points (keep)

| Extension | Source |
|-----------|--------|
| Workspace item kinds: one registered table; nothing branches on kind | `docs/architecture/workspace.md#workspace-items` |
| Overlay features and mark kinds | `frontend/src/present/README.md` |
| Sidebar views and viewers | `docs/architecture/ui-framework.md#sidebar-views`, `#viewer-widgets` |
| Overlay preferences: one closed set driving menu, settings and config | [08](08-settings-cli.md#settings-model) |
