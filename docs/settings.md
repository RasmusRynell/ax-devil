# Settings

**Settings** edits the active configuration file, which is selected at launch with `--config`:

- **General:** theme, text size, window title bar, video cache memory, overlay interaction, and
  keyboard shortcuts.
- **Stream defaults:** device credentials and RTSP defaults for new live streams.
- **Analytics:** defaults for MQTT and DataHub overlay connections.
- **Network scan:** startup discovery, sign-in defaults, the Live view's default IP range, and the logins its scan tries.
- **Storage:** the folders for window state, cache, logs, and render catalogs.
- **Advanced:** the active configuration's location and version.

**OK** saves all pages and closes, and **Cancel** discards the changes. When a saved change needs a restart, ax-devil
asks whether to restart now or later.
Stream defaults apply to newly opened live-stream dialogs. **Default IP range** (`defaults.network_scan.ip_range`) is the
addresses the Live view's **Scan settings** start with, such as `192.168.1.0/24`. **Sign-in logins**
(`defaults.network_scan.credentials`, a list of `username` and `password` pairs) are what **Try to sign in to found
cameras** tries on each camera, in order; **Add login** adds one, and a row without a username is dropped on save. The
Live view shows the same list under that choice, and saves its edits at once.
Theme and text size changes apply immediately; storage, title bar, and graphics changes need a restart. Changing storage locations does not move existing files.
Keyboard shortcuts are confirmed in their own dialog.

**Network scan → Discovery** has two options, both enabled by default. **Scan for cameras on startup**
(`settings.network_scan.on_startup`) starts one background scan when ax-devil launches, using the default IP range.
Startup scanning runs at reduced load; manual scans run at normal speed.
The Live view shows its progress and keeps its results; **Rescan** starts a fresh scan. Turning this off skips the scan
on future launches without disabling manual scans. **Try to sign in to found cameras by default**
(`settings.network_scan.sign_in`) starts the Live view's sign-in checkbox checked, including for the startup scan.
Turn it off to discover cameras without trying saved logins; it also stops the check of the workspace's cameras
against their own saved login. Changing the default applies to the Live view immediately;
you can also override its checkbox for a manual scan. Neither option interrupts a scan already running.

Stream defaults, Analytics, Network scan and Storage fields keep `$VARIABLE_NAME` references as written and read the variable when the value is
used; any other text is literal, even when it starts with `$` or `~`. Password references stay visible while literal
passwords are masked. Numeric and storage references must resolve to valid values to save. Storage folders may start
with `~` for the home folder.

## Quick Setup

On first start, **Quick Setup** opens. Choices apply as they are clicked and are saved when the dialog closes.
**Help → Quick Setup** opens it again. Closing it sets `ui.quick_setup_done` to `true`; set it to `false` to show it on
the next start.

## Text size

**General → Appearance → Text size** (`ui.text_size`) sets the body text size of the whole interface; headings,
captions, list rows, and title bars scale with it, while margins stay fixed. **System** (the default) uses the
operating system's interface font size; **Small**, **Medium**, **Large**, and **Larger** are 13, 15, 18, and 21 px.
The typeface always follows the operating system. Changes apply to open windows immediately.

## Video cache memory

**General → Playback → Memory for video caching** (`settings.playback.video_cache_total_mib`) sets one allowance shared
equally by all open offline videos; opening or closing a video redistributes it. More memory keeps more frames ready for
seeking. **Auto** (default) is 25% of the RAM available at startup, or 1 GiB if detection fails; **Manual** takes
0.25–1024 GiB. Memory is only used as needed, and saving resizes existing caches immediately. Decoder, display, and other
application memory come on top, so this is not a total RAM limit: with 1 GiB of cache, playback uses roughly 1.5–2.5 GiB
in total, and seeking in long-GOP 4K video can raise process memory by several GiB, which may stay resident. Auto does
not watch other applications or prevent swapping.

**Prepare the next video in the background** (`settings.playback.prepare_next_video`) is off by default. While a
playlist entry plays, it starts a separate process that warms the caches of the entry playback steps to next, so that
entry opens sooner. Turning it off stops a running process.

## Configuration and local data

Configuration is stored under `~/.ax_devil/configs/` by default; caches, logs, and render catalogs also
default to folders under `~/.ax_devil/`.

Keep these private:

- Configuration can store manually entered credentials as plain JSON.
- Logs may contain credentials or authenticated URLs. Redact them before sharing.
- Keep cache and config folders user-owned. Do not load third-party caches or distribute your own.

## Video overlays

**View → Show Lane Names** toggles the names drawn at the top of each video lane.
Names stay in place while zooming and hide only while frame info is shown.
Turning off **View → Object Hover and Click Details** also turns off the hover chip and selecting objects on the video.
Both preferences apply immediately to open viewers and are saved in the active configuration.
They are also available under **Settings → General → Video Overlays**.
