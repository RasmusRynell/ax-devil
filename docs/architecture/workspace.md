# Workspace

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
| Content | What an item resolves to, one or more per item: the immutable description a viewer opens. Rebuilt on every load, never saved. |
| Viewer Widget | An open inspection surface in the UI that shows Content. Not part of the Workspace. |

## Layers

```
Workspace file (JSON)  <->  Workspace + Workspace Items   ->  Content   ->  Viewer Widget
        saved                  workspace/core                resolved      workspace/ui, video_viewer
```

- Items are the saved truth. Content is derived from items and is never written to disk. Runtime sources are created
  only when a viewer opens Content.
- Session state — open panes, splits, current frame, play state, exclusions, window geometry — is not part of the
  Workspace, and a Workspace never depends on it. Exclusions are therefore not saved, so the file stays a pure list of
  recipes and nothing needs keys that survive a resolver re-run.
- `workspace/core` holds the model and the file format; `workspace/ui` is one Qt interface on top of it. The import
  rule between them is in [invariants](../domain/invariants.md#content-model).

## Workspace Items

Item kinds are open-ended: a kind is one `WorkspaceItem` subclass registered in `ITEM_KINDS`, and the Workspace, the
file format, and the store do not branch on kind.

- Content identity is derived, never random: the Content at position *i* of item *x* has `content_id` `x/i`, so
  resolving the same item again yields the same ids, and the UI can group and remove an item's Content as a whole.
- An item shows its `label`, or its default name when the label is empty: a video's file name, a stream's host with
  any `$VARIABLE` expanded, a playlist's names from its resolver. The default name is derived when shown and never
  saved. Renaming an item to an empty name, or to the name its row already shows while unlabeled, empties its label.
- A Live Stream Item keeps hosts, usernames, and passwords as entered; a `$VARIABLE` reference is expanded with the
  config's rule only while resolving.
- `UnreadableItem` stands in for an item this version cannot read. It never resolves, cannot be renamed, and is
  written back unchanged, so saving never loses it.

## Resolution

`WorkspaceStore` owns resolution. Every change to the items applies at once; their Content follows from `ItemResolver`,
which resolves on background threads because playlist resolvers search folders. Until then the item is pending and
its row opens nothing. A result for an item removed meanwhile, or for a Workspace since replaced, is dropped; a result
for an item renamed meanwhile is named after the new label. Renaming never resolves again: `ItemResolution` keeps the
Content as named without a label and names it after the item.

An item that fails to resolve stays in the Workspace with its reason, shown as one unavailable row; when the user added
it, they are also told at once. The Add Live Stream and Add Playlist dialogs resolve their item first and stay open
with the reason when it fails; on success they hand the result to the store, which does not resolve it again. CLI
commands only build items; their failures are reported in the app like any other added item's.

## Lifecycle

The app behaves like VS Code. `WorkspaceLifecycle` (`workspace/ui/workspace_lifecycle.py`) makes these decisions over
the store; it asks the user through `WorkspacePrompts` and builds no widgets.

- Quitting never prompts and keeps the current Workspace, unsaved edits included, in `workspace-backup.json` in the
  storage folder saved at that time. Credentials are kept as entered, as in a workspace file.
- Every launch first restores the kept Workspace. The file at its kept path, read again, is the saved state, so unsaved
  edits are still marked modified; when the file can no longer be read, the items stay as an Untitled Workspace.
- Restored, opened, and new Workspaces list their items and open and connect nothing; only adding items opens a viewer.
  A CLI launch with content then starts an Untitled Workspace with those items, and `ax-devil open <file>` opens a
  saved one, each after the usual question.
- Replacing a modified Workspace asks Save / Discard / Cancel. Saving an Untitled Workspace goes through Save As, and
  cancelling that cancels the whole action. A file that cannot be read or written is reported and changes nothing.
- Opened and saved files are listed as recent workspaces under the path the user chose; symlinks are not followed.

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

- There is one supported `version`; older files are not migrated before a stable release. A file that cannot be read
  as a whole raises one `WorkspaceFileError` with a user-facing message, and a failed save leaves the existing file
  unchanged.
- Video Item paths inside the workspace file's folder are written relative to it, others absolute. Paths are
  normalized lexically, so `..` cannot escape the folder; a symlinked workspace file is read and saved at its target.
- Playlist settings are opaque to the core and written exactly as given. A resolver that wants portable settings
  stores them relative itself.
- Credentials and hosts are written as stored: a `$VARIABLE` reference stays a reference, and a literal value is
  written literally, so a file holding literal credentials must be shared with care. New files are private to the
  user.
- An item that cannot be read — an unknown kind or malformed JSON — never fails the load; it becomes an
  `UnreadableItem`.

## Playlist Resolver Contract

Recipes need resolvers that run without a widget:

- `resolve(settings) -> list[PlaylistContent]` — headless, and run away from the GUI thread; `settings` is a
  JSON-serializable mapping. Settings that are invalid or point at nothing raise `ValueError` or `OSError` with a
  user-facing message.
- `create_settings_widget()` — edits settings and calls `submit_settings(settings)`.
- `create_cli_command()` — builds settings from CLI arguments and passes a `PlaylistItem` to
  `ctx.obj["run_with_items"]`.

The `write-plugin` skill's reference describes the plugin API. The built-in resolvers' settings:

| Resolver | Settings |
|----------|----------|
| `folder_pair` | `{"videos_dir": str, "overlays_dir": str, "handler_type": str}` |
| `mot_challenge` | `{"root": str, "sequences": [str, ...]}`; without `sequences`, every sequence under `root` (the CLI's choice) |
