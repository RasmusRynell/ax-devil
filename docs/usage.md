# Usage reference

Details behind the [README](../README.md) walkthrough. Commands are shown as `ax-devil`; from a source checkout,
prefix them with `uv run`.

## Workspaces

A workspace is the set of videos, live cameras and playlists you added. **File → Save Workspace** writes it to a
`.ax-devil.workspace` file; `ax-devil open <file>` and the File menu open one. The header of the Workspace view shows
its name, with `●` while it has unsaved changes. The name is saved in the file and is separate from the file's name,
which is what the recent lists show. A workspace is called Untitled until you name it.

ax-devil never asks about unsaved changes. They are kept as a copy in the storage folder's `unsaved-workspaces` folder
and listed among the recent workspaces; the workspace's own file is not touched. A plain `ax-devil` launch restores
them, with a banner offering to discard them, unless another window already has them open. `ax-devil local`, `live` and
`playlist` start a new Untitled workspace with what they open. The design is in [Workspace](architecture/workspace.md#lifecycle).

A workspace saves what you added, not how you looked at it: file paths (relative to the workspace file when they are
in its folder), live stream settings, playlist resolver settings and bookmarks (never on live streams). Open viewers, layouts, playback
positions and hidden entries are not saved. Camera and broker hosts and credentials are written as entered: a
`$VARIABLE` reference stays a reference, and a typed password is written as plain text, so share such files with care.

An item that cannot open, such as a missing file or a missing plugin, stays in the list with a warning icon; hover it
for the reason.

The Workspace, Live and Offline views have a search at the top, which matches what a row shows, a **Sort**, which
orders the rows of each section, and a **Filter**, which shows or hides rows by what they are, such as a found camera's
type. **Sort** and **Filter** appear once there is a choice to make. Right-click a playlist entry or an overlay and
choose **Hide** to skip it in playback; it stays listed, dimmed, until you choose **Unhide**. A playlist lists its
entries when it loads, unless it has more than 200; expand a longer one with its arrow.

## Sidebar views

- **Live**: a camera streams while its viewer is open, and starts only with **Start stream**, never by
  double-clicking it. It can start before ax-devil has checked its login. With sign-in on, a camera whose saved login
  is refused loses **Start stream** and shows why on hover.
  - A background scan starts at launch; **Scan settings** takes addresses and ranges such as `192.168.1.0/24`, and an
    empty field searches this computer's `/24` network. Startup scanning and sign-in can each be turned off in
    [Settings → Network scan](settings.md).
  - With **Try to sign in to found cameras**, the scan tries the saved sign-in logins (the list in **Settings →
    Network scan**) on each camera until one works. Logins go only to devices that answered as Axis devices, and only
    as an HTTP Digest answer, never as a plain password.
  - **Add** puts a signed-in camera in the workspace with the **Settings → Stream defaults** and the login that worked,
    and **Add manually** does the same for a camera you enter. Neither starts the stream.
- **Offline**: **Rescan** reads a playlist's folders again; open viewers keep what they show.
- **Plugins**: installs, updates and removals apply after a restart, which a banner offers.

## Video and overlays

A video can have any number of overlay files, and each one is a lane to compare. `ax-devil local --help` lists the
file-opening options and `ax-devil list-handlers` the available handlers.

## Live cameras

New connections use the credentials from **Settings → Stream defaults** and the overlay mode and decoder from
**Settings → Analytics**; `ax-devil live --help` lists overrides.

By default, credentials come from environment variables, which keeps them out of the configuration file:
`AX_DEVIL_TARGET_ADDR`, `AX_DEVIL_TARGET_USER` and `AX_DEVIL_TARGET_PASS` for the camera, and
`AX_DEVIL_MQTT_BROKER_ADDR`, `AX_DEVIL_MQTT_BROKER_USER` and `AX_DEVIL_MQTT_BROKER_PASS` for the MQTT broker. Any
**Stream defaults** or **Analytics** text field accepts a `$VARIABLE` reference in place of a value. Command-line
options override both.

DataHub defaults to HTTPS/WSS with certificate verification disabled: traffic is encrypted, but the device
identity is not authenticated. Use it only on trusted camera networks. HTTP/WS is selectable with no automatic
fallback, and redirects are rejected, so connect directly to the device host. This applies to DataHub only,
not RTSP or MQTT.

## Playlists

`ax-devil playlist --help` lists the resolvers. To write one, see the
[write-plugin skill](../.agents/skills/write-plugin/SKILL.md). Dataset walkthroughs:
[UVG-VCM](datasets/uvg-vcm.md) and [MOT Challenge](datasets/mot-challenge.md).

## Overlay appearance

Built-in catalogs are read-only; make a copy before editing. A catalog choice applies to the current view; the
**⋯** menu next to it applies it to all open views or makes it the default for new ones. For authoring, see the
[render-catalog skill](../.agents/skills/render-catalog/SKILL.md#how-a-catalog-is-built);
`ax-devil catalog reference` prints the full language.

## Overlay details

Click an object to pin the inspector; pinned values follow the displayed frame, so pause playback when copying text.

The **Details** choices in the media tools panel only change what a view draws; hidden data stays in inspection and
hover cards. Each view or comparison lane keeps its own choices when changing catalogs, and offline playlists retain
them by lane position across entries. Choices last until the viewer closes and are not saved across restarts. Export
uses the choices selected when it starts.

## Export

Exports preserve source frame timing and contain no audio. A failed export reports the error in the dialog and logs;
an existing destination file is replaced only after success.
