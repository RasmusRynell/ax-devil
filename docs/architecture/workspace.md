# Workspace

> Status: **proposed design**. Delivery step 1 (the `core`/`ui` split) is implemented; the rest is not; see [Delivery](#delivery).

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
- `label` — display name
- serialization to and from a JSON object, given the workspace folder for relative paths
- `resolve(context) -> tuple[Content, ...]` — rebuild the Content, or raise a resolution error with a user-facing
  reason. Video and Live Stream Items resolve to one Content; a Playlist Item resolves to whatever its resolver
  returns.

The registry is a `dict[str, type]` keyed by `kind`. Content records the id of the item it came from, so the UI can
group an item's Content and remove the item as a whole.

## Workspace

The Workspace is an immutable value: a name source (file path or none) and the ordered items.
Edits produce a new value. The UI store keeps the current Workspace and the last saved one, so "modified" is simply
`current != saved` — no dirty flag to keep in sync.

Exclusions — playlist entries and lanes the user hid from playback with the eye toggle — are session state, like the
current frame. They are not saved, so the file stays a pure list of recipes and nothing needs keys that survive a
resolver re-run.

## Lifecycle

The app behaves like VS Code:

- Launch reopens the last workspace, saved or Untitled. Its items are listed; nothing opens or connects on its own.
- Closing never prompts. The current workspace, including unsaved edits and an Untitled one, is kept in the storage
  directory and restored on the next launch, still marked modified.
- Opening or creating another workspace while the current one is modified asks Save / Discard / Cancel.
- A CLI launch with content (`ax-devil local`, `ax-devil live`, resolver commands) starts a fresh Untitled workspace;
  if the kept workspace is modified, the same Save / Discard / Cancel question comes first.
  `ax-devil open <file>.ax-devil.workspace` opens a saved workspace.
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
     "settings": {"root": "runs/exp3"}}
  ]
}
```

- `version` is required. There is one supported version; older files are not migrated before a stable release.
- Paths are written relative to the workspace file's folder when they are inside it, absolute otherwise.
- Credentials are written as entered. A `$VARIABLE` reference stays a reference, using the same rule as the config;
  a literal value is written literally, so a file holding literal credentials must be shared with care.
- An item that cannot be resolved on open — missing folder, offline camera, unknown kind, missing plugin — stays in
  the Workspace as unavailable, with its reason and its original JSON. Saving writes it back unchanged.

## Playlist Resolver Contract

Recipes need resolvers that run without a widget. The contract becomes:

- `resolve(settings) -> list[PlaylistContent]` — headless; `settings` is a JSON-serializable dict
- `create_settings_widget()` — edits and returns settings; it no longer emits Content
- `create_cli_command()` — builds settings from CLI arguments

The headless part lives in a Qt-free module that `workspace/core` imports; the widget part stays with the UI.
This changes the plugin API for external resolvers; `docs/plugins.md` and the `write-plugin` skill change with it.

## Removed By This Design

- `StartupContent`, `VideoFileStartup`, `LiveStreamStartup`, `ResolvedPlaylistStartup` — replaced by Workspace Items.
  The CLI, dialogs, file drops, and recents all create items.
- `WorkspaceManager` — replaced by the core Workspace value and the UI store.
- `WorkspaceBrowserRow` projection inside the state owner — moves to `workspace/ui`.
- Random `content_id` as the only identity — Content records the id of the item it came from.
- `WorkspaceWidget` — renamed `ViewerWidget` and moved to `workspace/ui`.
- `recent-videos.json` — replaced by recent workspaces.

## Delivery

Each step is one pull request stacked on the previous one. Each leaves the app working and replaces the old code and
tests outright rather than carrying both.

1. **Carve out** — create `workspace/core` and `workspace/ui`, move files, rename `WorkspaceWidget` to
   `ViewerWidget`, move browser rows to `ui`, add the no-PySide6 test for `core`. Done. Importing `content.py` loaded
   PySide6 through the eager `data_sources` and `synchronization` package `__init__` files, and through `logging_config`
   importing `QLoggingCategory`; each is now Qt-free. The plugin-backed decoder options (`plugin_intake`) moved to `ui`
   because the plugin system still imports Qt; the headless resolver contract in step 2 removes that dependency.
2. **Items** — Workspace Items, the Workspace value, stable ids, the headless resolver contract; startup types and
   `WorkspaceManager` removed; tests rewritten around items.
3. **File format** — JSON v1 load/save, relative paths, credential references, unavailable items.
4. **UI** — File → New / Open / Save / Save As, Untitled and modified state, `ax-devil open <file>`, recent
   workspaces on the welcome screen, unavailable items in the sidebar. Built into today's UI with minimal changes; a
   UI rebuild on top of `workspace/core` is a separate, later effort.
