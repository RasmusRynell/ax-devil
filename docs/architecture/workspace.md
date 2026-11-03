# Workspace

A Workspace is the collection of things a user is working with — videos, live streams, playlists — that can be saved
to a file and reopened to get back the same set of work. It plays the role a `.code-workspace` file plays in VS Code:
it records *what* you work with, not where your cursor was.

## Terms

| Term | Meaning |
|------|---------|
| Workspace | An ordered collection of Workspace Items. Exactly one is open at a time. It is *Untitled* until the user names it; the name is saved in the file and is not the file's name. |
| Workspace Item | A recipe for one thing to work with: enough to rebuild it, never the rebuilt result. Each item has a stable id and a kind. |
| Video Item | A video file with optional overlay files and decoders. |
| Live Stream Item | A camera stream with optional live overlay settings. |
| Playlist Item | A playlist resolver id plus that resolver's settings. Opening the workspace runs the resolver again; it may produce one or more playlists. |
| Bookmark | A frame the user marked in a Video Item or a playlist entry, with a kind and a note. Saved with the items, as are the targets marked reviewed. |
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
  Workspace, and a Workspace never depends on it. Exclusions are not saved; they name entries by their Bookmark Target
  entry, so they survive a re-run. Bookmarks are saved; see [Bookmarks](#bookmarks).
- `workspace/core` holds the model and the file format; `workspace/ui` is one Qt interface on top of it. The import
  rule between them is in [invariants](../domain/invariants.md#content-model).

## Workspace Items

Item kinds are open-ended: a kind is one `WorkspaceItem` subclass registered in `ITEM_KINDS`, and the Workspace, the
file format, and the store do not branch on kind.

- Content identity is derived, never random: a playlist of item *x* has `content_id` `x/<playlist name>` (its numbered
  `source_name`), other Content `x/0`, so resolving the same item again yields the same ids even when a resolver
  returns a different set of playlists.
- An item shows its `label`, or its default name when the label is empty: a video's file name, a stream's host with
  any `$VARIABLE` expanded, a playlist's names from its resolver. The default name is derived when shown and never
  saved. An empty name empties its label.
- A Live Stream Item keeps hosts, usernames, and passwords as entered; a `$VARIABLE` reference is expanded with the
  config's rule only while resolving.
- `UnreadableItem` stands in for an item this version cannot read. It never resolves, cannot be edited, and is
  written back unchanged, so saving never loses it.

## Bookmarks

While reviewing local videos and playlists, a user bookmarks frames and marks each video reviewed. Both are part of
the Workspace, beside the items, so they are saved and make the Workspace modified.

- A Bookmark Target is an item id plus an entry key that Content gives itself (`bookmark_entries`): empty for a Video
  Item, "<playlist name>/<entry title>" for a playlist entry. Entries have no id and their indexes shift when a
  resolver runs again, so the key is the only thing that survives a re-run. The playlist name is the one its resolver
  gave it (`PlaylistContent.source_name`), numbered when several share one, so renaming the item changes no key, and
  it tells the playlists of one Playlist Item apart. An entry's title is its label, numbered when several entries
  share one. A renamed or removed entry leaves its target orphaned. Orphaned targets and their bookmarks are kept;
  only removing their item removes them.
- A viewer reports the frame on screen as a `BookmarkPosition`, and opening a bookmark opens the Content at its entry
  and frame, paused, through `WorkspaceController.show_frame`, which reuses a viewer already showing that entry. Building
  the groups the Bookmarks view shows, their order, orphan detection and filtering are pure functions in
  `bookmark_rows.py`.
- Only an item whose `bookmarkable` is true takes new bookmarks or review changes (never a live stream; see
  [Sidebar](../domain/invariants.md#sidebar)). Bookmarks a file already holds load whatever their item, so ones on
  an `UnreadableItem` are saved back.

## Resolution

`WorkspaceStore` owns resolution. Every change to the items applies at once; their Content follows from `ItemResolver`,
which resolves on background threads because playlist resolvers search folders. Until then the item is pending and
its row opens nothing. A result for an item removed meanwhile, or for a Workspace since replaced, is dropped; a result
for an item renamed meanwhile is named after the new label. Renaming never resolves again: `ItemResolution` keeps the
Content as named without a label and names it after the item.

The store watches what each item was read from (`WorkspaceItem.source_paths`, plus the overlay files its Content
names, never video files) and resolves the item again when the
[`FileWatcher`](../../src/ax_devil/modules/file_watching/README.md) reports a change, so rows follow regenerated files.
Sources are fingerprinted before they are read, so a change made while resolving is still noticed. A new result, an
error included, replaces the old one and never opens a viewer or makes the Workspace modified. An open viewer
showing a changed overlay file is offered a reload and is never reloaded by itself.

An item that fails to resolve stays in the Workspace with its reason, shown as one unavailable row; when the user added
it, they are also told at once. The Add Live Stream and Add Playlist dialogs resolve their item first and stay open
with the reason when it fails; on success they hand the result to the store, which does not resolve it again. CLI
commands only build items; their failures are reported in the app like any other added item's.

## Lifecycle

The app behaves like VS Code. `WorkspaceLifecycle` (`workspace/ui/workspace_lifecycle.py`) makes these decisions over
the store; it asks the user through `WorkspacePrompts` and builds no widgets.

- Nothing prompts on launch, open, New or quit. A CLI launch with content starts an Untitled Workspace with those
  items, and `ax-devil open <file>` opens a saved one.
- A Workspace with unsaved edits has one recovery copy in `unsaved-workspaces` in the storage folder saved at that
  time, rewritten in place every couple of seconds and listed first among the recent workspaces. It is a workspace
  file that also names the file it recovers (`workspace_file`, null when Untitled), so its edits are never written to
  the Workspace's own file. Saving or discarding deletes it; opening another Workspace or New, and quitting, keep it.
  Credentials are saved as entered, as in a workspace file.
- A plain launch restores the newest recent workspace if it is a copy, as a modified Workspace with a banner offering
  Keep, or Discard, which reopens the Workspace's own file or starts an empty one. Opening a copy from the recent workspaces
  restores it the same way. An instance locks its copy (`<copy>.lock`), so another running instance starts empty or,
  asked to open it, says it is open in another window.
- Opened and new Workspaces list their items and open and connect nothing; only adding items opens a viewer.
- Saving a Workspace that has no file goes through Save As, and cancelling that cancels the save. A file that cannot be
  read or written is reported and changes nothing.
- Opened and saved files are listed as recent workspaces under the path the user chose; symlinks are not followed.
  Each recent keeps the time it was last opened or saved, so listing and dating recents never reads their files; for
  the same reason a recent is labelled with its file's name, not the name saved inside it.

## File Format

Workspace files use the `.ax-devil.workspace` extension and contain JSON:

```json
{
  "version": 1,
  "name": "Parking lots",
  "items": [
    {"kind": "video", "id": "8f3c…", "label": "Parking lot", "video": "clips/lot.mp4",
     "overlays": [{"path": "clips/lot.json", "decoder": "adf-v1"}]},
    {"kind": "live_stream", "id": "1a9e…", "label": "Entrance", "host": "$AX_DEVIL_TARGET_ADDR",
     "username": "$AX_DEVIL_TARGET_USER", "password": "$AX_DEVIL_TARGET_PASS", "camera_head": 1},
    {"kind": "playlist", "id": "c04d…", "label": "Exp 3", "resolver": "mot_challenge",
     "settings": {"root": "/data/runs/exp3"}}
  ],
  "bookmarks": [
    {"id": "5b2e…", "item": "c04d…", "entry": "MOT Challenge/MOT17-02", "frame": 120, "time": 4.0, "kind": "issue",
     "note": "ID switch"}
  ],
  "reviewed": [{"item": "8f3c…", "entry": ""}]
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
- A malformed bookmark or reviewed target, or one naming an item the file does not hold, fails the load instead of
  being dropped. Files without `bookmarks` or `reviewed` read them as empty.

## Playlist Resolver Contract

Recipes need resolvers that run without a widget:

- `resolve(settings, token) -> list[PlaylistContent]` — headless, and run away from the GUI thread; `settings` is a
  JSON-serializable mapping. Settings that are invalid or point at nothing raise `ValueError` or `OSError` with a
  user-facing message.
- `create_settings_widget()` — edits settings and calls `submit_settings(settings)`; its optional restoration hook is
  defined on [`PlaylistResolverWidget`](../../src/ax_devil/modules/plugin_system/playlist_resolvers.py).
- `create_cli_command()` — builds settings from CLI arguments and passes a `PlaylistItem` to
  `ctx.obj["run_with_items"]`.

The `write-plugin` skill's reference describes the plugin API.
