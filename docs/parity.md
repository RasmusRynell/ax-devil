# Parity

One line per parity row of [01](rebuild/01-product-scope.md), which defines each row and its priority (M needed for
parity, S may follow with owner consent). Status: todo, in progress, done, dropped. Dropping an M row needs the
owner's written decision; a new feature gets a row in 01 first. Update a line in the commit that changes its status,
with the test that proves it.

## Workspace

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| WS-01 | Ordered items (video, live stream, playlist); Untitled until named | M | in progress | `crates/workspace/src/intake.rs`, `crates/workspace-store` tests, `src/session/project.rs`, `tests/local_video.rs`, `tests/mot_playlist.rs` | Video and playlist items, in memory; no live streams, naming or saving (M3) |
| WS-02 | New, Open, Save, Save As, recent workspaces | M | todo |  |  |
| WS-03 | No prompts; recovery copy, restore banner, per-instance lock | M | todo |  |  |
| WS-04 | Unreadable items preserved | M | todo |  |  |
| WS-05 | Background resolution; pending and unavailable rows | M | in progress | `crates/workspace-store` tests, `frontend/src/viewer/ViewerHost.test.tsx`, `frontend/src/shell/WorkspaceTree.test.tsx`, `tests/local_video.rs` | Items resolve in the background; a pending or failed one has its sidebar row and a failed one keeps its reason. Left: the invariant says an unavailable row never opens, but its row opens a viewer that shows the reason |
| WS-06 | Re-resolve on file change; reload offered to open viewers | M | todo |  |  |
| WS-07 | Rename, Edit, Remove, item information | M | todo |  |  |
| WS-08 | Hide/Unhide entries and lanes | M | done | `crates/workspace-store` tests, `crates/sessions/tests/session/matrix.rs`, `src/session/project.rs`, `frontend/src/shell/WorkspaceTree.test.tsx`, `frontend/src/viewer/{PlaylistViewer,hidden}.test.*`, `frontend/src/playback/{LaneGrid,VideoViewer}.test.tsx` | Kept in the Doc with the session, never saved ([CONTEXT](../CONTEXT.md)). The row menu and the lane menu hide; "Show hidden" lists hidden rows dimmed; next/previous skip hidden entries and hidden lanes are not drawn |
| WS-09 | Bookmarks and reviewed marks; Bookmarks view; open at frame | M | todo |  |  |
| WS-10 | Bookmarks to CSV | S | todo |  |  |
| WS-11 | Sidebar views Workspace, Live, Offline, Bookmarks, Plugins; search, sort, filter | M | in progress | `shell/Shell.test.tsx`, `shell/WorkspaceTree.test.tsx` | The Workspace view is a tree of items, entries and lanes from the backend's rows. The other views and the search fields are placeholders; no sort or filter |
| WS-12 | Start panel and welcome area | M | in progress | `shell/Shell.test.tsx` | Add Video and Add Playlist are wired; workspace open, live intake and recent workspaces remain unfinished |
| WS-13 | Split viewer area: drag to split, preview vs pinned panes | M | todo |  |  |
| WS-14 | Long playlists load collapsed | S | todo |  |  |
| WS-15 | Hover cards with thumbnails | S | todo |  |  |

## Adding content

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| AC-01 | Add Video with any number of overlays; decoder picked by extension | M | in progress | `crates/workspace/src/intake.rs`, `src/cli/tests.rs`, `tests/playback_ws.rs`, `tests/local_video.rs`, `desktop/args.test.cjs`, `frontend/src/shell/Shell.test.tsx` | `local --video V [--overlay P]... [--handler-type H]...` and the intake take any number of overlays, each lane with its decoder (by extension when none is named). File > Add Video (the local window shows the shell's file dialog, a remote window or browser asks for a path on the server; the frontend sends the paths as a Session command) takes no overlays yet; the native dialog itself has no automated test |
| AC-02 | Drop video files on the window | M | todo |  |  |
| AC-03 | Add Live Stream with analytics discovery | M | todo |  |  |
| AC-04 | Add Playlist through a resolver form | M | done | `frontend/src/shell/AddPlaylistDialog.test.tsx`, `frontend/src/resolvers/*.test.*`, `crates/playlist-resolvers/tests/schema.rs`, `src/routes/handlers.rs`, `src/cli/tests.rs`, `tests/local_video.rs`, `tests/mot_playlist.rs`, `make tests-desktop` | The form and the `playlist <resolver>` command are generated from the resolver's declared schema; scan is cancellable. Adding sends a command: the playlist resolves in the background and a failure shows on its row, and only the intake's refusal stays in the dialog. The command line's playlist waits in the launch queue for the first local window |
| AC-05 | One intake validation for dialogs, CLI and drops | M | in progress | `crates/workspace/src/intake.rs`, `tests/local_video.rs`, `tests/mot_playlist.rs` | The dialogs and the CLI use `workspace::intake`, for videos with overlays and for playlists; drops are not built |

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
| OF-01 | Play/pause, step, speed, seek; clock-following playback | M | in progress | `frontend/src/playback/{clock,direction,session,FrameBuffer,protocol,wire}.test.ts`, `Transport.test.tsx`, `VideoViewer.test.tsx`, `tests/playback_ws.rs`, `crates/foundation` time tests, `crates/media/tests/decode.rs`, `make tests-desktop` | Exact-frame number entry is still missing (the frame readout is not editable); seek/step work. No reverse play. Image sequences play through the same path (`crates/media/tests/sequence.rs`). Variable frame rate, open at a frame and per-frame errors included. The desktop test plays a fixture on Xvfb; playback on a real GPU was measured in the D0 spike, not by a committed test |
| OF-02 | Comparison lanes: several overlays on one video; a playlist entry switches the video, and two recordings side by side are two viewers with no shared clock (out of scope) | M | done | `tests/mot_playlist.rs`, `tests/playback_lanes.rs`, `crates/overlays/src/tests/`, `frontend/src/playback/{LaneGrid,lanes,session,OverlayBuffer}.test.*` | Lanes of one entry share the video's frames and clock; see the deviation below |
| OF-03 | Playlists of entries and lanes; next/previous entry | M | done | `crates/playlist-resolvers/tests/`, `crates/workspace/src/intake.rs`, `tests/mot_playlist.rs`, `frontend/src/viewer/{PlaylistViewer,PlaylistFollower}.test.tsx`, `crates/sessions/tests/session/revocation.rs`, `make tests-desktop` | MOT Challenge and folder-pair playlists; one lane per overlay file. A following window moves with the presenter's entry |
| OF-04 | Timestamp matching, sequence-keyed files, sticky overlays | M | done | `crates/overlay-sync/src/select/tests.rs`, `crates/overlays/src/tests/views.rs`, `tests/mot_playlist.rs` | Per lane, session only; dense and sparse coverage in [invariants](domain/invariants.md#frame-identity) |
| OF-05 | Timing and alignment diagnostics per lane | M | todo |  |  |
| OF-06 | Decoded-frame cache under one memory allowance | M | in progress | `crates/playback/src/tests/{cache,planner,decode,delivery}.rs`, `tests/playback_ws.rs`, `src/video_cache.rs`, `crates/settings` tests | Allowance, equal shares, eviction, live resize and uncached oversized frames work. Left: the cache marks on the timeline (M4); next-entry warm-up is OF-07 |
| OF-07 | Prepare next playlist entry in the background | S | todo |  |  |
| OF-08 | Export with overlays: lane selection, quality presets, cancellable | M | todo |  |  |

### Offline playback: deviations from the reference

The decisions taken with the owner, with their reasons, are in
[14](rebuild/14-build-order.md#decisions-taken-for-offline-video). The rest:

| Row | Deviation | Reason |
|-----|-----------|--------|
| OF-02 | Lanes share one video: the reference let a secondary lane have its own video, length and frame rate, aligned to the primary lane's frame index | D19: a viewer shows one video; two recordings side by side are two viewers |
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
| IN-02 | Object filters by type and score/confidence range; whole-file filtering with declared policies | M | in progress | `crates/scene/src/filter.rs`, `frontend/src/playback/LaneMenu.test.tsx`, `tests/mot_playlist.rs` | Class toggles per lane from the decoder's declared policy. Left: score and confidence ranges, whole-file filtering |
| IN-03 | Hover chip and click selection; can be turned off | M | todo |  |  |
| IN-04 | Frame info HUD, lane names | M | in progress | `frontend/src/playback/VideoViewer.test.tsx`, `make tests-desktop` | Each lane's name is on its tag. Left: the frame info HUD |
| IN-05 | Zoom, pan, reset, synced across lanes; lane and app fullscreen | M | todo |  |  |
| IN-06 | Per-view overlay detail visibility | M | todo |  |  |
| IN-07 | Per-view sticky/sync/filter settings, Apply to all, defaults for new views | S | in progress | `frontend/src/playback/{LaneMenu,lanes}.test.*`, `crates/overlays/src/tests/views.rs` | Sticky, sync and class filters are set per lane and kept while the entry is open. Left: Apply to all, defaults for new views |
| IN-08 | Structured query search in Objects and Events lists | M | todo |  |  |
| IN-09 | Frame vs File scope for objects | M | todo |  |  |
| IN-10 | Pinned fields kept across object pages | S | todo |  |  |
| IN-11 | Copy a field or section value | S | todo |  |  |
| IN-12 | Presence strip with seeking; previous/next appearance | M | todo |  |  |
| IN-13 | Event to frame (offline; live events have no seekable history); event/object cross-navigation | M | todo |  |  |

## Render catalogs

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| RC-01 | Catalog language (schema v3) and the six built-ins | M | done | `crates/render-catalog/tests/{compile.rs,render/}`, `crates/catalogs` tests | All six built-ins compile and match the reference's Drawing Instructions on the oracle sheets (`fixtures/oracle`) |
| RC-02 | Per-view selection, Apply to all, Use as default, hot reload | M | in progress | `frontend/src/playback/LaneMenu.test.tsx`, `tests/playback_lanes.rs`, `crates/overlays/src/tests/views.rs` | A lane picks one of the built-ins from its menu, listed in `Opened`; a catalog that fails keeps the last one. Left: Apply to all, Use as default, user catalogs and hot reload |
| RC-03 | Catalog viewer window with seeded sheets, New copy, Delete | M | todo |  |  |
| RC-04 | `catalog list/check/render/new/use/reference` | M | in progress | `src/cli/tests.rs`, `crates/catalogs/src/check.rs` | `catalog list` and `catalog check`; the others are not built |

## Plugins

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| PL-01 | Decoder plugins (file, payload) and playlist resolver plugins | M | in progress | `crates/handlers`, `crates/scene-decoders/tests/registry.rs` | The built-ins sit behind `Handlers`, the one registry a plugin runtime will extend (M7); no plugin loads yet |
| PL-02 | Built-in decoders and resolvers listed by `list-handlers` and `playlist --help` | M | in progress | `src/cli/tests.rs`, `src/routes/handlers.rs`, `crates/handlers` tests | MOT, UVG-VCM, ONVIF XML and CVAT; both resolvers. ADF beta/v1 frame, consolidated and payload decoders remain. CLI help is generated; no test renders it |
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
| ST-05 | Keyboard shortcuts, dialog, override persistence | M | done | `crates/settings` tests, `frontend/src/shell/ShortcutsDialog.test.tsx`, `Shell.test.tsx` | Command table, override-only storage, conflict and reserved-key checks, the Shortcuts dialog and live key handling; action coverage follows each feature row (image zoom is still IN-05) |
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

## Sharing and remote use (new)

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| SH-01 | Join, reconnect and revoke access | M | in progress | `crates/sessions/tests/session/`, `tests/session_socket.rs`, `tests/playback_ws.rs` | Lifecycle, leases and tickets implemented; real-network interruption evidence remains |
| SH-02 | Follow or explore independently | M | in progress | `frontend/src/viewer/PlaylistFollower.test.tsx`, `desktop/smoke-sharing.cjs` | Video and playlist following/exploration; inspection and live policies land with those features |
| SH-03 | Audience privacy and settings scope | M | in progress | `tests/access.rs`, `src/session/project.rs`, `crates/settings/src/scope_tests.rs` | Host-path projections and personal/server settings implemented; future inspection data needs its own disclosure acceptance |
| SH-04 | Browser and remote Electron connection | M | in progress | `desktop/smoke-remote.cjs`, `frontend/src/session/Notice.test.tsx` | Server frontend and password recovery implemented; LAN failure feedback, running-app launch delivery and packaged protocol registration remain |

## Developer tooling

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| DT-01 | Isolated GUI test environment for agents and CI | M | in progress | `make tests-desktop` | `scripts/isolated`: private home and display, user-file guard; Linux |
| DT-02 | UI screenshot harness | M | in progress | `make tests-desktop` | `desktop/smoke*.cjs`: main window, View menu, Quick Setup, the Settings (including Storage) and Shortcuts dialogs, the Add Playlist dialog, and the fixture playlist's lanes (overlay pixels asserted) in light and dark, at the window minimum and 200% zoom, plus a video and a sharing scenario; other windows land with M8 |
| DT-03 | Rendering benchmarks with A/B against a ref | S | in progress | `make bench` | `render-catalog` and `overlays` benches on a 650-box frame; numbers on stdout, nothing asserted and no A/B against a ref |
| DT-04 | Timestamp alignment report | S | todo |  |  |
| DT-05 | Release check in clean environments | S | todo |  |  |
| DT-06 | Demo recorder | S | todo |  |  |
