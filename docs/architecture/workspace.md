# Workspace

> Status: **implemented**. How it was delivered is summarized under [Delivery](#delivery).

A Workspace is the collection of things a user is working with — videos, live streams, playlists — that can be saved
to a file and reopened to get back the same set of work. It plays the role a `.code-workspace` file plays in VS Code:
it records *what* you work with, not where your cursor was.

## Terms

| Term | Meaning |
|------|---------|
| Workspace | An ordered collection of Workspace Items. Exactly one is open at a time. It is *Untitled* until saved; once saved, its name is its file name. |
| Workspace Item | A recipe for one thing to work with: enough to rebuild it, never the rebuilt result. Each item has a stable id and a kind. |
| Video Item | A video file with optional overlay files and decoders. |
| Live Stream Item | A camera stream with optional live overlay settings. |
| Playlist Item | A playlist resolver id plus that resolver's settings. Opening the workspace runs the resolver again; it may produce one or more playlists. |
| Content | What an item resolves to, one or more per item: the immutable description a viewer opens (`SeekableVideoContent`, `LiveVideoContent`, `PlaylistContent`). Rebuilt on every load, never saved. |
| Viewer Widget | An open inspection surface in the UI that shows Content. Not part of the Workspace. |

## Layers

```
Workspace file (JSON)  <->  Workspace + Workspace Items   ->  Content   ->  Viewer Widget
        saved                  workspace/core                resolved      workspace/ui, video_viewer
```

- Items are the saved truth. Content is derived from items and is never written to disk.
- Runtime sources are still created only when a viewer opens Content.
- Session state — open panes, splits, current frame, play state, exclusions, window geometry — is not part of the
  Workspace. It may be restored separately later, but a Workspace never depends on it.

## Package Layout

```
modules/workspace/
  core/   Workspace, Workspace Items, item kinds, file format, resolution, Content, intake
  ui/     store with Qt signals, sidebar and browser rows, split view, ViewerWidget, dialogs, welcome screen
```

Rules:

- `workspace/core` imports nothing from PySide6 and nothing from `workspace/ui`, directly or through other modules.
  A test imports `core` in a fresh interpreter and fails if any PySide6 module was loaded.
- `workspace/ui` is one UI on top of the core; a new UI must be buildable from `core` alone.
- Code that another module needs from `ui` moves out to a shared module instead of being imported across.

## Workspace Items

Item kinds are open-ended. Adding a kind means adding one class and registering it; the Workspace, the file format,
and the store do not branch on kind. Video, Live Stream, and Playlist are built in; the registry is shaped so a plugin
hook for new kinds can be added later without changing the core.

Every item kind provides:

- `kind` — the stable string written to the file, e.g. `"video"`, `"live_stream"`, `"playlist"`
- `id` — a stable item id, created once and saved
- `label` — the name the user gave it, or empty; `display_name` is the label, or else `default_name`, and it names
  the item's Content
- `default_name` — the name derived from the recipe for display, never saved: a Video Item's file name, a Live Stream
  Item's host with any `$VARIABLE` expanded, a Playlist Item's resolver id (its playlists keep the names their
  resolver gives them)
- `to_json(base_dir)` and `from_json(data, base_dir)` — the item as a JSON object, given the workspace folder for
  relative paths; without one, every path is written absolute
- `resolve(context) -> tuple[Content, ...]` — rebuild the Content, or raise `ItemResolutionError` with a user-facing
  reason. Video and Live Stream Items resolve to one Content; a Playlist Item resolves to whatever its resolver
  returns. A Video Item whose files are missing fails to resolve.

Items are frozen dataclasses deriving from `WorkspaceItem` (`workspace/core/items.py`). A kind implements one hook,
`_build_contents(context)` and two JSON hooks for its own fields, `_fields_to_json` and `_fields_from_json`; the shared
`to_json` and `from_json` add and read `kind`, `id`, and `label`; `default_name` is a property each kind answers. The
shared `resolve` turns `ValueError` and `OSError` into `ItemResolutionError`, rejects an empty result, and gives every
Content its identity. The registry, `ITEM_KINDS`, is a `dict[str, type]` keyed by `kind`.

Content identity is derived, never random: the Content at position *i* of item *x* has `content_id` `x/i` and
`item_id` `x`, so resolving the same item again yields the same ids, and the UI can group an item's Content and remove
the item as a whole. Resolvers build Content without ids; resolution assigns them.

The `ResolutionContext` protocol (`workspace/core/resolution.py`) gives `resolve` what it needs: the `WorkspaceIntake`
and `playlist_resolver(resolver_id)`. Core never imports the plugin system; the plugin-backed context lives in
`workspace/ui/plugin_intake.py`.

A Live Stream Item keeps the device and MQTT broker hosts, usernames, and passwords as entered. A `$VARIABLE` reference
stays a reference in the item and is expanded with the config's rule only while resolving; the CLI reads those config
defaults raw, and the Add Live Stream dialog fills empty fields with the raw defaults. Items added by the CLI and
the Add dialogs have an empty label unless the user typed a name, so the expanded host is only ever shown, never
stored. Renaming an item to an empty name, or to the name its browser row already shows while unlabeled, empties its label
again. A labeled Playlist Item that
resolves to one playlist names it after the item; several are named `label / playlist name`.

## Workspace

The Workspace is an immutable value (`workspace/core/workspace.py`): a name source (file path or none) and the ordered
items. Edits — add items, remove or rename an item by id — produce a new value. The UI store, `WorkspaceStore`
(`workspace/ui/workspace_store.py`), keeps the current Workspace and the last saved one, so "modified" is simply
`current != saved` — no dirty flag to keep in sync. Items may hold settings mappings, so workspaces are compared, never
hashed. A new store holds the empty Untitled Workspace as both. `open_workspace(path)` loads a file, resolves its
items, and makes it both current and saved, or raises `WorkspaceFileError` and changes nothing; `save_workspace(path)`
writes the current Workspace (to its own file when no path is given) and makes it saved. The store emits
`workspace_replaced` when another Workspace is installed, so views drop what no longer exists (viewers of items that
are gone close), and `state_changed` whenever the modified flag, name, or path changes, so a title bar needs no
polling.

The store resolves items as they are added and keeps each result, `ItemResolution`: the item, its Content, and the
error. Renaming never resolves again: the kept base Content is named from the new label (`WorkspaceItem.name_contents`),
with the same content ids and exclusions, and an empty label shows the default name. An item that fails to resolve
stays in the Workspace, the user is told why when adding it, and the sidebar shows it as one unavailable row with a
warning icon and the reason in its tooltip and information; activating it shows the reason instead of opening. It can
be renamed and removed, except that an `UnreadableItem` cannot be renamed (`renamable` is false), since it is written
back unchanged. The Add Live Stream and Add Playlist dialogs resolve their item before accepting, so their errors keep
the dialog open instead.

Exclusions — playlist entries and lanes the user hid from playback with the eye toggle — are session state, like the
current frame. They are not saved, so the file stays a pure list of recipes and nothing needs keys that survive a
resolver re-run.

## Lifecycle

The app behaves like VS Code. `WorkspaceSession` (`workspace/ui/session.py`) owns the lifecycle; the questions and
file choices go through `WorkspacePrompts` (`workspace/ui/workspace_prompts.py`), which tests replace.

- Closing never prompts. Whenever the app quits — closing, restarting, or quitting after an error — `MainWindow`
  has `WorkspaceBackup` (`workspace/core/workspace_backup.py`) keep the current workspace in
  `workspace-backup.json` in the storage directory: its file path, or null, and its items in the file format's item
  JSON with absolute paths. Credentials are kept as entered, as in a workspace file.
- Every launch first restores the kept workspace. The file at the kept path, read again, becomes the saved state and
  the kept items the current one, so unsaved edits are still marked modified; an Untitled workspace's saved state is
  the empty Untitled one. When the kept file can no longer be read, its items stay as an Untitled workspace and the
  reason is logged. Restored items are listed; nothing opens or connects on its own. Only adding items opens a viewer,
  the first new item's Content; installing another Workspace closes every viewer. A restart from Settings keeps the
  global options of the command line but restores the kept workspace instead of reopening its content. The backup and
  recent-workspace files use the storage folder saved now, so a changed folder applies from the next launch.
- **File → New Workspace**, **Open Workspace** (`Ctrl+O`), **Open Recent**, and the welcome screen's recent list ask
  Save / Discard / Cancel when the current workspace is modified. Save on an Untitled workspace goes through Save As;
  cancelling it cancels the whole action. **Save Workspace** (`Ctrl+S`) saves an Untitled workspace through **Save
  Workspace As** (`Ctrl+Shift+S`), which appends `.ax-devil.workspace` when the chosen name lacks it and then asks
  before replacing an existing file of that name. A file that cannot be read or written is reported and changes
  nothing.
- A CLI launch with content (`ax-devil local`, `ax-devil live`, resolver commands) then starts a fresh Untitled
  workspace with those items, after the same question when the kept workspace is modified; Cancel keeps the kept
  workspace without the CLI's items. `ax-devil open <file>.ax-devil.workspace` opens a saved workspace the same way.
- `RecentWorkspaces` (`workspace/core/recent_workspaces.py`) records every opened and saved file in
  `recent-workspaces.json` in the storage directory, newest first, at most eight, under the absolute path the user chose
  (symlinks are not followed); files that no longer exist are dropped.
- The main window title shows the workspace name and `●` while it is modified, following `state_changed`.
- Items can be added, removed, and renamed. Editing an item's recipe is not part of the first version.

## File Format

Workspace files use the `.ax-devil.workspace` extension and contain JSON:

```json
{
  "version": 1,
  "items": [
    {"kind": "video", "id": "8f3c…", "label": "Parking lot", "video": "clips/lot.mp4",
     "overlays": [{"path": "clips/lot.json", "decoder": "adf-v1"}]},
    {"kind": "live_stream", "id": "1a9e…", "label": "Entrance", "host": "$AX_DEVIL_TARGET_ADDR",
     "username": "$AX_DEVIL_TARGET_USER", "password": "$AX_DEVIL_TARGET_PASS", "camera_head": 1},
    {"kind": "playlist", "id": "c04d…", "label": "Exp 3", "resolver": "mot_challenge",
     "settings": {"root": "/data/runs/exp3"}}
  ]
}
```

- `version` is required. There is one supported version; older files are not migrated before a stable release. A
  missing or other version, invalid JSON, a top level that is not an object, or `items` that is not a list makes
  loading raise one `WorkspaceFileError` with a user-facing message, as does a repeated item id.
- A failed save leaves the existing file unchanged.
- Paths a Video Item holds are written relative to the workspace file's folder (with `/` separators) when they are
  inside it, absolute otherwise; relative paths are read against that folder. If the workspace file is a symlink,
  that folder is the one holding the file it points to, and saving replaces that file, not the link. Both sides are
  normalized lexically first (`..` collapses, symlinks are not resolved), so an escaping `..` segment makes a path absolute.
- Playlist settings are opaque to the core: they are written and read exactly as given, with no path rewriting. A
  resolver that wants portable settings stores them relative itself.
- A Live Stream Item writes every field. Credentials and hosts are written as stored: a `$VARIABLE` reference stays a
  reference, using the same rule as the config; a literal value is written literally, so a file holding literal
  credentials must be shared with care. Fields missing from the file take their defaults.
- An item that cannot be read — an unknown kind, or a known kind with malformed JSON — never fails the load. It
  becomes an `UnreadableItem`: it keeps the raw `id` (or gets a new one) and a label from the raw `label` (or the
  kind), resolves to an `ItemResolutionError` giving the reason, and is written back unchanged on save. Unreadable
  items compare by their raw JSON, not their id, so reading the same file twice gives equal workspaces. The
  Workspace, the store, and the UI treat it as any item that failed to resolve. An item that reads fine but fails to
  resolve on open — missing folder, offline camera, missing plugin — is an ordinary item with a recorded error.

## Playlist Resolver Contract

Recipes need resolvers that run without a widget. The contract is:

- `resolve(settings) -> list[PlaylistContent]` — headless; `settings` is a JSON-serializable mapping. Missing or
  invalid settings, or settings that point at nothing, raise `ValueError` or `OSError` with a user-facing message.
- `create_settings_widget()` — edits settings and calls `submit_settings(settings)`; it no longer emits Content
- `create_cli_command()` — builds settings from CLI arguments and passes a `PlaylistItem` to `ctx.obj["run_with_items"]`

The headless part is the `PlaylistResolver` protocol in `workspace/core/resolution.py`, which `PlaylistResolverPlugin`
satisfies; the widget part stays with the UI. This changes the plugin API for external resolvers; the `write-plugin`
skill's reference describes it.

The built-in resolvers' settings:

| Resolver | Settings |
|----------|----------|
| `folder_pair` | `{"videos_dir": str, "overlays_dir": str, "handler_type": str}` |
| `mot_challenge` | `{"root": str, "sequences": [str, ...]}`; without `sequences`, every sequence under `root` (the CLI's choice) |

## Removed By This Design

- `StartupContent`, `VideoFileStartup`, `LiveStreamStartup`, `ResolvedPlaylistStartup` — replaced by Workspace Items.
  The CLI, dialogs, and file drops all create items.
- `PlaylistResolverWidget.playlist_resolved` and `emit_playlists` — resolver widgets submit settings instead.
- `WorkspaceManager` — replaced by the core Workspace value and the UI store.
- `WorkspaceBrowserRow` projection inside the state owner — moves to `workspace/ui`.
- Random `content_id` as the only identity — Content records the id of the item it came from.
- `WorkspaceWidget` — renamed `ViewerWidget` and moved to `workspace/ui`.

## Delivery

The design was delivered in four stacked pull requests, each replacing the old code and tests outright.

1. **Carve out** — create `workspace/core` and `workspace/ui`, move files, rename `WorkspaceWidget` to
   `ViewerWidget`, move browser rows to `ui`, add the no-PySide6 test for `core`. Done. Importing `content.py` loaded
   PySide6 through the eager `data_sources` and `synchronization` package `__init__` files, and through `logging_config`
   importing `QLoggingCategory`; each is now Qt-free. The plugin-backed decoder options (`plugin_intake`) moved to `ui`
   because the plugin system still imports Qt; the headless resolver contract in step 2 removes that dependency.
2. **Items** — Workspace Items, the Workspace value, stable ids, the headless resolver contract; startup types and
   `WorkspaceManager` removed; tests rewritten around items. Done. Offline lanes now share a frame source by video
   value rather than by id, since lane videos inside a playlist carry no identity of their own, and viewers are
   tracked by the item their Content came from.
3. **File format** — JSON v1 load/save, relative paths, credential references, unreadable items. Done. Each item kind
   serializes itself; the store opens and saves files and announces replaced and modified state.
4. **UI** — File → New / Open / Save / Save As, Untitled and modified state, `ax-devil open <file>`, recent
   workspaces on the welcome screen, unavailable items in the sidebar, rename, and default names. Done, built into
   the existing UI with minimal changes; a UI rebuild on top of `workspace/core` is a separate, later effort.
