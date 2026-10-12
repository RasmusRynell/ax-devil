# Usage reference

Details behind the [README](../README.md) walkthrough. Commands are shown as `ax-devil`; from a source checkout,
prefix them with `uv run`.

## Workspaces

The **Workspace** view of the sidebar lists the workspace under **Live**, **Videos**, and **Playlists**. Under a camera is its address, and under two items with the same name the folder that tells them apart; hover
an item for more, such as how many entries of a playlist take part. An accent edge marks what is open in a viewer. While the workspace is empty the sidebar shows
buttons to open content and your recent workspaces instead. **File → Save Workspace**
(`Ctrl+S`) saves it as a `.ax-devil.workspace` file. **File → Open Workspace** (`Ctrl+O`), **File → Open Recent**, the
sidebar's **Recent workspaces** list, and `ax-devil open <file>` open one. The Workspace view's header shows the
workspace name, with `●` while it has unsaved changes; click the name to change it. The name is saved in the workspace
file and is separate from the file's name, which is what the recent lists show.

Closing never asks. The workspace, unsaved changes included, is kept in the storage folder and reopened by the next
plain `ax-devil` launch, with its items listed and nothing opened or connected. Opening or creating another workspace
while the current one has unsaved changes asks whether to save them first. `ax-devil local`, `live`, and `playlist`
start a new Untitled workspace with what they open. A workspace is called Untitled until you name it.

A workspace saves what you added, not how you looked at it: file paths (relative to the workspace file when they are
in its folder), live stream settings, and playlist resolver settings. Open viewers, layouts, playback positions, and
hidden entries are not saved. Camera and broker hosts and credentials are written as entered: a `$VARIABLE` reference
stays a reference, and a typed password is written as plain text, so share such files with care.

Right-click an item to rename or remove it; an empty name returns to its default name, such as the file name or the
camera host. An item that cannot open, such as a missing file or a missing plugin, stays in the list with a warning
icon; hover it for the reason. The details are in [Workspace](architecture/workspace.md#lifecycle).

The Workspace, Live and Offline views have a search at the top, which matches what a row shows, and a **Filter**,
which shows or hides rows by what they are, such as a found camera's type. Rows left out of playback start hidden;
check **Left out** in the **Filter** to list them.

## Sidebar views

You are always in a workspace. The strip along the left edge has two parts. The top buttons are the workspace: the
first shows everything in it, and the others show one part of it, with their own actions. The bottom buttons are the
application, and stay the same whichever workspace is open: **Plugins**, the render catalogs and **Settings**.

- **Live** lists the workspace's cameras with **Connect** and **Disconnect**; a camera is connected while its viewer is
  open. Right-click a camera for the same menu as in the Workspace view, such as **Rename** and **Remove**. The search
  matches found cameras also by serial number and firmware, and the cameras in the workspace too once a scan found
  them, which then show what it learned on hover. **Sort** orders the cameras the scan found, and **Filter** narrows
  them by type and sign-in. **Scan settings**, at the bottom, takes the IP addresses to search: addresses
  and ranges such as `192.168.1.0/24`, separated by commas. **Rescan** looks for Axis cameras there, only when you
  press it, or on this computer's `/24` network when the field is empty, and lists each camera as soon as it answers.
  With **Sign in to found cameras**, the scan also signs in, with the username and password you enter or else the
  **Settings → Stream defaults**, to read more such as the SoC and to tell whether the credentials work. They go only
  to devices that answered as Axis devices, over HTTPS, or over HTTP as a Digest answer, never as a plain password.
  **Add** puts a found camera in the workspace with the **Settings → Stream defaults**, and with the scan's credentials
  when they signed in to it. **Add manually** opens the Add Live Stream dialog, as **File → Add Live Stream** does.
- **Offline** lists the workspace's data from files: its videos, with or without overlays, and its playlists.
  **Rescan** reads a playlist's folders again, for example after adding files; open viewers keep what they show.
  **Add from** lists the playlist resolvers, including those from plugins; **Add** opens the Add Playlist dialog with
  that resolver chosen.
- **Bookmarks** lists the frames you marked in local videos and playlists, grouped by video or playlist entry.
  **View → Add Bookmark** (`M`) marks the frame on screen. Click a bookmark to open it paused at its frame; right-click
  to rename it, change its kind (Note, Issue, Example, Question) or remove it. The chips and the search filter the
  list, and a group's **Reviewed** / **To review** mark toggles when clicked. **Export CSV** writes every bookmark.
  Bookmarks and review marks are saved in the workspace file, and live streams cannot be bookmarked.
- **Plugins** shows which plugins loaded, which failed and why, and which are built in, and under each plugin the
  decoders and playlist resolvers it gives you, with whether a decoder reads overlay files, live messages or both.
  **Add** installs a package, wheel or folder; the `…` menu of an installed plugin updates or removes it; the reload
  button next to **Add** loads plugins again.
  Installs, updates and removals apply after a restart, which a banner offers. **Debug → Plug-ins** also opens it.
  A small warning triangle appears on the button's icon while a plugin has failed to load.

## Video and overlays

`ax-devil local --help` lists file-opening options. `--overlay` requires `--handler-type`;
`ax-devil list-handlers` lists the available values.

The **media tools panel** holds filtering, object inspection, the event log, the render catalog choice and export.
Open it with **View → Media Tools Panel** (`Ctrl+B`) or by double-clicking or dragging the handle on the right edge of the video.
**View → Sidebar** (`Ctrl+\`), or the button of the view on screen in the strip along the left edge, hides or shows the
sidebar. The same strip opens the render catalogs and Settings.

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
