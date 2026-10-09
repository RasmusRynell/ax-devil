# Settings

**Settings** edits the active configuration file, which is selected at launch with `--config`:

- **General:** theme, text size, window title bar, graphics acceleration, video cache memory, overlay interaction, and
  keyboard shortcuts.
- **Stream defaults:** device credentials and defaults for RTSP, MQTT, and DataHub connections.
- **Storage:** window state, cache, log, and render catalog folders.
- **Advanced:** the active configuration's location and version.

**OK** saves all pages and closes, and **Cancel** discards the changes. When a saved change needs a restart, ax-devil
asks whether to restart now or later.
Stream defaults apply to newly opened live-stream dialogs.
Theme and text size changes apply immediately; storage, title bar, and graphics changes need a restart. Changing storage locations does not move existing files.
Keyboard shortcuts are confirmed in their own dialog.

Connection and storage fields keep `$VARIABLE_NAME` references as written. Password references stay visible
while literal passwords are masked. Numeric and storage references must resolve to valid values to save.

## Quick Setup

On first start, **Quick Setup** asks for the theme and text size. Choices apply as they are clicked and are saved
when the dialog closes. **Help → Quick Setup** opens it again. Closing it sets `ui.quick_setup_done` to `true`; set it
to `false` to show it on the next start.

## Text size

**General → Appearance → Text size** (`ui.text_size`) sets the body text size of the whole interface; headings,
captions, list rows, and title bars scale with it, while margins stay fixed. **System** (the default) uses the
operating system's interface font size; **Small**, **Medium**, **Large**, and **Larger** are 13, 15, 18, and 21 px.
The typeface always follows the operating system. Changes apply to open windows immediately.

## Graphics acceleration

**General → Appearance → Graphics acceleration** (`settings.appearance.graphics_acceleration`):

- **Auto** (default): acceleration where needed; Qt chooses the graphics backend. Ordinary dialogs and menus use
  Qt's normal widget presentation.
- **Off**: software rendering, for troubleshooting graphics problems.

It affects video and overlay rendering and window presentation, not video decoding. An explicit `QT_WIDGETS_RHI`
environment variable overrides Qt's widget presentation policy, and the dialog shows when that is set. Setting it
to `1` forces acceleration even for ordinary dialogs and menus. For window preparation, see
[Display Backends](architecture/draw-system.md#display-backends).
Auto does not detect incompatible drivers or fall back automatically; if startup fails, follow the
[software-rendering recovery steps](installation.md#graphics-startup-problems).

## Video cache memory

**General → Playback → Memory for video caching** (`settings.playback.video_cache_total_mib`) sets one allowance shared
equally by all open offline videos; opening or closing a video redistributes it. More memory keeps more frames ready for
seeking. **Auto** (default) is 25% of the RAM available at startup, or 1 GiB if detection fails; **Manual** takes
0.25–1024 GiB. Memory is only used as needed, and saving resizes existing caches immediately. Decoder, display, and other
application memory come on top, so this is not a total RAM limit: with 1 GiB of cache, playback uses roughly 1.5–2.5 GiB
in total, and seeking in long-GOP 4K video can raise process memory by several GiB, which may stay resident. Auto does
not watch other applications or prevent swapping.

## Configuration and local data

Configuration is stored under `~/.ax_devil/configs/` by default; caches, logs, and render catalogs also
default to folders under `~/.ax_devil/`.

Keep these private:

- Configuration can store manually entered credentials as plain JSON.
- Logs may contain credentials or authenticated URLs, and performance traces contain local paths.
  Redact diagnostics before sharing.
- Frame caches use pickle. Keep cache and config folders user-owned. Do not load third-party caches
  or distribute your own.

## Video overlays

**View → Show Lane Names** toggles the names drawn at the top of each video lane.
Names hide while zoomed in or while frame info is shown, and return afterwards.
**View → Object Hover and Click Details** toggles object highlighting, hover cards, and click-to-pin details.
Both preferences apply immediately to open viewers and are saved in the active configuration.
They are also available under **Settings → General → Video Overlays**.
