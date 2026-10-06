# Settings

**Settings** edits the active configuration file, which is selected at launch with `--config`:

- **General:** theme, window title bar, graphics acceleration, video cache memory, overlay interaction, and
  keyboard shortcuts.
- **Stream defaults:** device credentials and defaults for RTSP, MQTT, and DataHub connections.
- **Storage:** window state, cache, log, and render catalog folders.
- **Advanced:** the active configuration's location and version.

**Apply** saves all pages, **OK** saves and closes, and **Cancel** discards changes since the last Apply.
Theme changes apply immediately; stream defaults apply to newly opened live-stream dialogs.
Storage, title bar, and graphics changes need a restart. Changing storage locations does not move existing files.
Keyboard shortcuts are confirmed in their own dialog.

Connection and storage fields keep `$VARIABLE_NAME` references as written. Password references stay visible
while literal passwords are masked. Numeric and storage references must resolve to valid values to save.

## Graphics acceleration

**General → Appearance → Graphics acceleration** (`settings.appearance.graphics_acceleration`):

- **Auto** (default): accelerated presentation; Qt chooses the graphics backend.
- **Off**: software rendering, for troubleshooting graphics problems.

It affects video and overlay rendering and window presentation, not video decoding. A `QT_WIDGETS_RHI`
environment variable overrides it for window presentation, and the dialog shows when that is set.
Auto does not detect incompatible drivers or fall back automatically; if startup fails, follow the
[software-rendering recovery steps](installation.md#graphics-startup-problems).

## Video cache memory

**General → Playback → Memory for video caching** sets one allowance shared equally by all open offline
videos; opening or closing a video redistributes it. **Auto** (default) is 25% of the RAM available at startup;
**Manual** takes a size in GiB. Memory is only used as needed, and Apply resizes existing caches immediately.
Decoder, display, and other application memory come on top, so this is not a total RAM limit.
See [playback memory measurements](runbooks/playback-memory.md).

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
