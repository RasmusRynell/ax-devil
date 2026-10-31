# Parity

One line per parity row of [01](rebuild/01-product-scope.md), which defines each row and its priority (M needed for
parity, S may follow with owner consent). Status: todo, in progress, done, dropped. Dropping an M row needs the
owner's written decision; a new feature gets a row in 01 first. Update a line in the commit that changes its status,
with the test that proves it.

## Workspace

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| WS-01 | Ordered items (video, live stream, playlist); Untitled until named | M | todo |  |  |
| WS-02 | New, Open, Save, Save As, recent workspaces | M | todo |  |  |
| WS-03 | No prompts; recovery copy, restore banner, per-instance lock | M | todo |  |  |
| WS-04 | Unreadable items preserved | M | todo |  |  |
| WS-05 | Background resolution; pending and unavailable rows | M | todo |  |  |
| WS-06 | Re-resolve on file change; reload offered to open viewers | M | todo |  |  |
| WS-07 | Rename, Edit, Remove, item information | M | todo |  |  |
| WS-08 | Hide/Unhide entries and lanes | M | todo |  |  |
| WS-09 | Bookmarks and reviewed marks; Bookmarks view; open at frame | M | todo |  |  |
| WS-10 | Bookmarks to CSV | S | todo |  |  |
| WS-11 | Sidebar views Workspace, Live, Offline, Bookmarks, Plugins; search, sort, filter | M | todo |  |  |
| WS-12 | Start panel and welcome area | M | todo |  |  |
| WS-13 | Split viewer area: drag to split, preview vs pinned panes | M | todo |  |  |
| WS-14 | Long playlists load collapsed | S | todo |  |  |
| WS-15 | Hover cards with thumbnails | S | todo |  |  |

## Adding content

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| AC-01 | Add Video with any number of overlays; decoder picked by extension | M | todo |  |  |
| AC-02 | Drop video files on the window | M | todo |  |  |
| AC-03 | Add Live Stream with analytics discovery | M | todo |  |  |
| AC-04 | Add Playlist through a resolver form | M | todo |  |  |
| AC-05 | One intake validation for dialogs, CLI and drops | M | todo |  |  |

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
| OF-01 | Play/pause, step, speed, seek; clock-following playback | M | todo |  |  |
| OF-02 | Comparison lanes: several overlays on one video, or lanes with different videos (playlist entries) | M | todo |  |  |
| OF-03 | Playlists of entries and lanes; next/previous entry | M | todo |  |  |
| OF-04 | Timestamp matching, sequence-keyed files, sticky overlays | M | todo |  |  |
| OF-05 | Timing and alignment diagnostics per lane | M | todo |  |  |
| OF-06 | Decoded-frame cache under one memory allowance | M | todo |  |  |
| OF-07 | Prepare next playlist entry in the background | S | todo |  |  |
| OF-08 | Export with overlays: lane selection, quality presets, cancellable | M | todo |  |  |

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
| ST-01 | Settings dialog pages; restart marker | M | todo |  |  |
| ST-02 | Quick Setup on first start | M | todo |  |  |
| ST-03 | Theme and text size, applied live | M | todo |  |  |
| ST-04 | `$VARIABLE` references | M | todo |  |  |
| ST-05 | Keyboard shortcuts, dialog, override persistence | M | todo |  |  |
| ST-06 | Workspace credential references expand only for their bound host or a confirmed one; warn before saving a literal password (new) | M | todo |  |  |
| AP-01 | Uncaught errors logged; continue or close | M | todo |  |  |
| AP-02 | Restart from inside the app | M | todo |  |  |
| AP-03 | `clear-cache` | S | todo |  |  |
| AP-04 | About dialog | S | todo |  |  |
| AP-05 | Custom window frame option | S | todo |  |  |
| AP-06 | Open logs folder / copy diagnostics (new) | S | todo |  |  |

## Developer tooling

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| DT-01 | Isolated GUI test environment for agents and CI | M | in progress | `make tests-desktop` | `scripts/isolated`: private home and display, user-file guard; Linux |
| DT-02 | UI screenshot harness | M | in progress | `make tests-desktop` | `desktop/smoke.cjs`: main window, light and dark; text sizes and other windows land with M0/M8 |
| DT-03 | Rendering benchmarks with A/B against a ref | S | todo |  |  |
| DT-04 | Timestamp alignment report | S | todo |  |  |
| DT-05 | Release check in clean environments | S | todo |  |  |
| DT-06 | Demo recorder | S | todo |  |  |
