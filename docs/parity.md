# Parity

One line per parity row of [01](rebuild/01-product-scope.md), which defines each row and its priority (M needed for
parity, S may follow with owner consent). Status: todo, in progress, done, dropped. Dropping an M row needs the
owner's written decision; a new feature gets a row in 01 first. Update a line in the commit that changes its status,
with the test that proves it.

## Workspace

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| WS-01 | Ordered items (video, live stream, playlist); Untitled until named | M | in progress | `crates/workspace/src/intake.rs`, `crates/workspace-store` tests, `tests/local_video.rs` | Video items only, in memory; no naming or saving (M3) |
| WS-02 | New, Open, Save, Save As, recent workspaces | M | todo |  |  |
| WS-03 | No prompts; recovery copy, restore banner, per-instance lock | M | todo |  |  |
| WS-04 | Unreadable items preserved | M | todo |  |  |
| WS-05 | Background resolution; pending and unavailable rows | M | in progress | `crates/workspace-store` tests, `frontend/src/viewer/ViewerHost.test.tsx`, `tests/local_video.rs` | Items resolve in the background and a failed one stays with its reason, shown in the viewer area; no sidebar rows yet |
| WS-06 | Re-resolve on file change; reload offered to open viewers | M | todo |  |  |
| WS-07 | Rename, Edit, Remove, item information | M | todo |  |  |
| WS-08 | Hide/Unhide entries and lanes | M | todo |  |  |
| WS-09 | Bookmarks and reviewed marks; Bookmarks view; open at frame | M | todo |  |  |
| WS-10 | Bookmarks to CSV | S | todo |  |  |
| WS-11 | Sidebar views Workspace, Live, Offline, Bookmarks, Plugins; search, sort, filter | M | in progress | `shell/Shell.test.tsx` | Views and search fields are placeholders; no items, sort or filter |
| WS-12 | Start panel and welcome area | M | in progress | `shell/Shell.test.tsx` | Layout only; the buttons and rows do nothing yet and recent workspaces are empty |
| WS-13 | Split viewer area: drag to split, preview vs pinned panes | M | todo |  |  |
| WS-14 | Long playlists load collapsed | S | todo |  |  |
| WS-15 | Hover cards with thumbnails | S | todo |  |  |

## Adding content

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| AC-01 | Add Video with any number of overlays; decoder picked by extension | M | in progress | `tests/local_video.rs`, `desktop/args.test.cjs`, `frontend/src/shell/Shell.test.tsx` | Video only, no overlays (M2). `local --video` and File > Add Video (the shell shows the file dialog, the frontend sends the paths as a Session command) share the intake; the native dialog itself has no automated test |
| AC-02 | Drop video files on the window | M | todo |  |  |
| AC-03 | Add Live Stream with analytics discovery | M | todo |  |  |
| AC-04 | Add Playlist through a resolver form | M | todo |  |  |
| AC-05 | One intake validation for dialogs, CLI and drops | M | in progress | `crates/workspace/src/intake.rs`, `tests/local_video.rs` | The dialog and the CLI use `workspace::intake`; drops are not built |

## Live

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| LV-01 | RTSP video with one overlay: embedded ONVIF metadata, MQTT or DataHub | M | todo |  |  |
| LV-02 | Per-feed status: connected, reconnecting, error, stalled; Retry | M | todo |  |  |
| LV-03 | Pause keeps the connection; stop is terminal | M | todo |  |  |
| LV-04 | Capture-time synchronization of frames and overlays | M | todo |  |  |
| LV-05 | Network scan with optional sign-in; add found camera | M | todo |  |  |
| LV-06 | Streams start only on explicit action | M | todo |  |  |
| LV-07 | Live Events list keeps recording while hidden; no seekable history | M | todo |  |  |
| LV-08 | Temporary device configuration (MQTT analytics publisher) removed on stop and quit | M | todo |  |  |

## Offline playback and comparison

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| OF-01 | Play/pause, step, speed, seek; clock-following playback | M | done | `frontend/src/playback/{clock,direction,session,FrameBuffer,protocol,wire}.test.ts`, `Transport.test.tsx`, `VideoViewer.test.tsx`, `tests/playback_ws.rs`, `crates/foundation` time tests, `crates/media/tests/decode.rs`, `make tests-desktop` | Video only: no overlays (M2), lanes (OF-02) or reverse play. Variable frame rate, open at a frame and per-frame errors included. The desktop test plays a fixture on Xvfb; playback on a real GPU was measured in the D0 spike, not by a committed test |
| OF-02 | Comparison lanes: several overlays on one video; a playlist entry switches the video, and two recordings side by side are two viewers with no shared clock (out of scope) | M | todo |  |  |
| OF-03 | Playlists of entries and lanes; next/previous entry | M | todo |  |  |
| OF-04 | Timestamp matching, sequence-keyed files, sticky overlays | M | todo |  |  |
| OF-05 | Timing and alignment diagnostics per lane | M | todo |  |  |
| OF-06 | Decoded-frame cache under one memory allowance | M | in progress | `crates/playback/src/tests/{cache,planner,decode,delivery}.rs`, `tests/playback_ws.rs`, `src/video_cache.rs`, `crates/settings` tests | Allowance, equal shares, eviction, live resize and uncached oversized frames work. Left: the cache marks on the timeline (M4); next-entry warm-up is OF-07 |
| OF-07 | Prepare next playlist entry in the background | S | todo |  |  |
| OF-08 | Export with overlays: lane selection, quality presets, cancellable | M | todo |  |  |

### Offline playback: deviations from the reference

The decisions taken with the owner, with their reasons, are in
[14](rebuild/14-build-order.md#decisions-taken-for-offline-video). The rest:

| Row | Deviation | Reason |
|-----|-----------|--------|
| OF-06 | The frame cache lives in `playback`, not `media` | Eviction follows playback windows; export, thumbnails and live never use the cache |
| OF-06 | Eviction takes the frame farthest from the playhead outside the protected windows; the reference was LRU | It suits scrubbing: after a jump, the frames around the old position are the least likely to be wanted |
| OF-06 | Auto is never below 256 MiB; the reference had no floor | It is the smallest value the setting accepts, so a machine with little free memory still gets a working cache |
| OF-01 | The index is built by scanning packets; the reference decoded the whole video. The decode scan is the fallback | The spike: 1.1 s against 36 s for 10 minutes of 1080p30, with identical results on every fixture |
| OF-01 | The index is valid by a stat fingerprint plus a digest of the first and last 64 KiB, not a full content hash; the reference used the stat fingerprint alone | Hashing a multi-gigabyte video would defeat instant reopen |
| OF-01 | Walking back through a group longer than the budget decodes a block of half the budget at a time | A keyframe group is never decoded again to keep one more frame |
| OF-01 | The client compares the protocol version in `Opened` with its own and ends the session on a mismatch | A stale window stops with a message instead of misparsing frames |
| OF-01 | Playback commands sit in a View > Playback submenu; the keys, including Left and Right for 10 frames, are the reference's | The menus are File, View, Debug and Help; there is no Playback menu |

## Inspection and display

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| IN-01 | Media tools panel per lane with Objects and Events lists, object and event pages | M | todo |  |  |
| IN-02 | Object filters by type and score/confidence range; whole-file filtering with declared policies | M | todo |  |  |
| IN-03 | Hover chip and click selection; can be turned off | M | todo |  |  |
| IN-04 | Frame info HUD, lane names | M | todo |  |  |
| IN-05 | Zoom, pan, reset, synced across lanes; lane and app fullscreen | M | todo |  |  |
| IN-06 | Per-view overlay detail visibility | M | todo |  |  |
| IN-07 | Per-view sticky/sync/filter settings, Apply to all, defaults for new views | S | todo |  |  |
| IN-08 | Structured query search in Objects and Events lists | M | todo |  |  |
| IN-09 | Frame vs File scope for objects | M | todo |  |  |
| IN-10 | Pinned fields kept across object pages | S | todo |  |  |
| IN-11 | Copy a field or section value | S | todo |  |  |
| IN-12 | Presence strip with seeking; previous/next appearance | M | todo |  |  |
| IN-13 | Event to frame (offline; live events have no seekable history); event/object cross-navigation | M | todo |  |  |

## Render catalogs

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| RC-01 | Catalog language (schema v3) and the six built-ins | M | todo |  |  |
| RC-02 | Per-view selection, Apply to all, Use as default, hot reload | M | todo |  |  |
| RC-03 | Catalog viewer window with seeded sheets, New copy, Delete | M | todo |  |  |
| RC-04 | `catalog list/check/render/new/use/reference` | M | todo |  |  |

## Plugins

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| PL-01 | Decoder plugins (file, payload) and playlist resolver plugins | M | todo |  |  |
| PL-02 | Built-in decoders and resolvers listed by `list-handlers` and `playlist --help` | M | todo |  |  |
| PL-03 | Install, update, remove, list from CLI and Plugins view | M | todo |  |  |
| PL-04 | Load failures never stop the app and are listed | M | todo |  |  |
| PL-05 | Unchanged third-party Python plugins run (bridge); not planned by default | S | todo |  |  |

## Settings and application

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| ST-01 | Settings dialog pages; restart marker | M | in progress | `frontend/src/shell/SettingsDialog.test.tsx`, `Shell.test.tsx` | Done: the dialog is generated from the settings table (pages, sections, `live` restart marker), with rows `ui.theme`, `ui.quick_setup_done`, `playback.video_cache` and `storage.cache_dir` (a folder field with Browse; the restart banner shows after a save). Remaining: the other settings, each with its consumer in M4/M5 |
| ST-02 | Quick Setup on first start | M | done | `frontend/src/shell/QuickSetupDialog.test.tsx`, `Shell.test.tsx` | Built from the rows marked `quick_setup` (the theme; the camera protocol joins in M5), opens on the first run and from Help, and each pick is stored live |
| ST-03 | Theme (light, dark, auto), applied live; no text-size setting, zoom is the browser's | M | done | `crates/settings` tests, `App.test.tsx` | Stored through `/api/settings`; the frontend applies the theme live |
| ST-04 | `$VARIABLE` references | M | in progress | `crates/settings` tests | Backend done: rows with `env` store the reference and expand it where used; no setting takes one yet |
| ST-05 | Keyboard shortcuts, dialog, override persistence | M | done | `crates/settings` tests, `frontend/src/shell/ShortcutsDialog.test.tsx`, `Shell.test.tsx` | Command table, override-only storage, conflict and reserved-key checks, the Shortcuts dialog and live key handling |
| ST-06 | Workspace credential references expand only for their bound host or a confirmed one; warn before saving a literal password (new) | M | in progress | `frontend/src/shell/SettingsDialog.test.tsx` | The Settings dialog warns under a secret field holding a literal (tested against a mocked secret row). Left: the first real secret row, `defaults.device.password`, and the workspace half, both in M5 |
| AP-01 | Uncaught errors logged; continue or close | M | todo |  |  |
| AP-02 | Restart from inside the app | M | todo |  |  |
| AP-03 | `clear-cache` | S | todo |  |  |
| AP-04 | About dialog | S | todo |  |  |
| AP-05 | Custom window frame: in-page title bar with window controls | S | done | `shell/WindowControls.test.tsx` | Frameless Electron window; dragging and maximize on a real window manager are unverified |
| AP-06 | Open logs folder / copy diagnostics (new) | S | todo |  |  |

### Settings and application: deviations

| Row | Deviation | Reason |
|-----|-----------|--------|
| ST-01 | `storage.cache_dir` takes no `$NAME` reference; [08](rebuild/08-settings-cli.md) asked for references in storage fields | Settings are personal to the user and Browse yields an absolute path; a reference would have to resolve before the first window opens |

## Developer tooling

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| DT-01 | Isolated GUI test environment for agents and CI | M | in progress | `make tests-desktop` | `scripts/isolated`: private home and display, user-file guard; Linux |
| DT-02 | UI screenshot harness | M | in progress | `make tests-desktop` | `desktop/smoke.cjs`: main window, View menu, Quick Setup, and the Settings (including Storage) and Shortcuts dialogs in light and dark, at the window minimum and 200% zoom, plus a video and a sharing scenario; other windows land with M8 |
| DT-03 | Rendering benchmarks with A/B against a ref | S | todo |  |  |
| DT-04 | Timestamp alignment report | S | todo |  |  |
| DT-05 | Release check in clean environments | S | todo |  |  |
| DT-06 | Demo recorder | S | todo |  |  |
