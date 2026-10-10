# Usage reference

Details behind the [README](../README.md) walkthrough. Commands are shown as `ax-devil`; from a source checkout,
prefix them with `uv run`.

## Workspaces

The sidebar lists the workspace: the videos, live streams, and playlists you added. **File → Save Workspace**
(`Ctrl+S`) saves it as a `.ax-devil.workspace` file. **File → Open Workspace** (`Ctrl+O`), **File → Open Recent**, the
welcome screen's **Recent** list, and `ax-devil open <file>` open one. The window title shows the workspace name, with
`●` while it has unsaved changes.

Closing never asks. The workspace, unsaved changes included, is kept in the storage folder and reopened by the next
plain `ax-devil` launch, with its items listed and nothing opened or connected. Opening or creating another workspace
while the current one has unsaved changes asks whether to save them first. `ax-devil local`, `live`, and `playlist`
start a new Untitled workspace with what they open.

A workspace saves what you added, not how you looked at it: file paths (relative to the workspace file when they are
in its folder), live stream settings, and playlist resolver settings. Open viewers, layouts, playback positions, and
hidden entries are not saved. Camera and broker hosts and credentials are written as entered: a `$VARIABLE` reference
stays a reference, and a typed password is written as plain text, so share such files with care.

Right-click an item to rename or remove it; an empty name returns to its default name, such as the file name or the
camera host. An item that cannot open, such as a missing file or a missing plugin, stays in the list with a warning
icon; hover it for the reason. The details are in [Workspace](architecture/workspace.md#lifecycle).

## Video and overlays

`ax-devil local --help` lists file-opening options. `--overlay` requires `--handler-type`;
`ax-devil list-handlers` lists the available values.

The **media tools panel** holds filtering, object inspection, the event log, the render catalog choice and export.
Open it with **View → Media Tools Panel** (`Ctrl+B`) or by double-clicking or dragging the handle on the right edge of the video.
**View → Sidebar** (`Ctrl+\`) hides or shows the list of opened content on the left.

Press **F** on a lane to show it fullscreen; **F** or **Esc** restores it, and **F11** toggles the application
window. Playback shortcuts can be changed in **Settings → General**.

## Live cameras

New connections use the credentials, overlay mode, and decoder from **Settings → Stream defaults**;
`ax-devil live --help` lists explicit overrides.

By default, credentials come from environment variables, which keeps them out of the configuration file:

| Variable | Used for |
|---|---|
| `AX_DEVIL_TARGET_ADDR`, `AX_DEVIL_TARGET_USER`, `AX_DEVIL_TARGET_PASS` | Camera host, username, and password |
| `AX_DEVIL_MQTT_BROKER_ADDR`, `AX_DEVIL_MQTT_BROKER_USER`, `AX_DEVIL_MQTT_BROKER_PASS` | MQTT broker host, username, and password |

```bash
export AX_DEVIL_TARGET_ADDR=192.168.1.100 AX_DEVIL_TARGET_USER=root AX_DEVIL_TARGET_PASS=secret
ax-devil live
```

Any **Stream defaults** text field accepts a `$VARIABLE` reference in place of a value. Command-line options override
both.

DataHub defaults to HTTPS/WSS with certificate verification disabled: traffic is encrypted, but the device
identity is not authenticated. Use it only on trusted camera networks. HTTP/WS is selectable with no automatic
fallback, and redirects are rejected, so connect directly to the device host. This applies to DataHub only,
not RTSP or MQTT.

## Playlists

`ax-devil playlist --help` lists the available resolvers; each resolver has its own `--help`, for example
`ax-devil playlist folder_pair --help`. To write a resolver, see the
[write-plugin skill](../.agents/skills/write-plugin/SKILL.md).

The [UVG-VCM walkthrough](datasets/uvg-vcm.md) pairs the dataset's official MP4s with native JSON annotations,
including tracking IDs and available segmentation polygons.

The [MOT Challenge walkthrough](datasets/mot-challenge.md) opens MOT17 and MOT20 sequences, with each public
detector and the ground truth as comparison lanes on the same frames.

## Overlay appearance

Built-in catalogs are read-only; make a copy before editing. A catalog choice applies to the current view; the
**⋯** menu next to it applies the choice to all open views or makes it the default for new ones. **Reload**
rereads the catalog if a file change was not detected.

`ax-devil catalog --help` lists validation, rendering, and catalog-management commands. For authoring,
see the [render-catalog skill](../.agents/skills/render-catalog/SKILL.md#how-a-catalog-is-built);
`ax-devil catalog reference` prints the full language.

## Overlay details

Hover an object for its full ID, object-specific details, and debug data grouped by source path. Click the object
to pin the inspector, scroll through all its fields, or select and copy text. Click empty video to unpin it.
Pinned values follow the displayed frame; pause playback when copying text.
The inspector grows to fit its contents, with a text-scaled minimum size and a readable width limit. Its height is
limited only by the video viewer's available space; scrolling starts when the contents exceed that space.

In the media tools panel, open **Details** beside **Catalog** to choose which information this view draws: object
outlines, IDs, class names, confidence, speed arrows, movement badges, attributes, or relations. Changes also update
paused video. For example, hide Confidence in a lane showing human annotations and keep it enabled for model
predictions in another lane. Hidden data remains available in inspection and hover cards.

Each view or comparison lane keeps its own choices when changing catalogs; unsupported details are disabled.
**Reset** enables everything. Offline playlists retain choices by lane position across entries. Choices last until
the viewer closes and are not saved across app restarts. Export uses the choices selected when export starts.

## Export

The media tools panel's **Export video with overlays** button opens the export dialog. Exports preserve source frame
timing and contain no audio. A failed export reports the error in the dialog and logs; an existing destination
file is replaced only after success.
