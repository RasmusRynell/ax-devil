# Parity

One line per parity row of [01](rebuild/01-product-scope.md), which defines each row and its priority (M needed for parity,
S may follow with owner consent). Status: todo, in progress, done, dropped; dropping an M row needs the
owner's written decision. A new feature gets a row in 01 first.

## Workspace

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| WS-01 | Ordered items (video, live stream, playlist); Untitled until named | M | in progress | `crates/workspace/src/intake.rs`, `crates/workspace-store` tests, `src/session/project.rs`, `tests/{local_video,mot_playlist}.rs` | Video and playlist items, in memory; no live streams, naming or saving (M3) |
| WS-02 | New, Open, Save, Save As, recent workspaces | M | todo |  |  |
| WS-03 | No prompts; recovery copy, restore banner, per-instance lock | M | todo |  |  |
| WS-04 | Unreadable items preserved | M | todo |  |  |
| WS-05 | Background resolution; pending and unavailable rows | M | in progress | `crates/workspace-store` tests, `frontend/src/{viewer/ViewerHost,shell/WorkspaceTree}.test.tsx`, `tests/local_video.rs` | A pending or failed item has a sidebar row; a failed one keeps its reason. Left: an unavailable row should not open (invariants) but opens a viewer showing the reason |
| WS-06 | Re-resolve on file change; reload offered to open viewers | M | todo |  |  |
| WS-07 | Rename, Edit, Remove, item information | M | todo |  |  |
| WS-08 | Hide/Unhide entries and lanes | M | done | `crates/workspace-store` tests, `crates/sessions/tests/session/matrix.rs`, `src/session/project.rs`, `frontend/src/{shell/WorkspaceTree,viewer/{PlaylistViewer,hidden},playback/{LaneGrid,VideoViewer}}.test.*` | Kept in the Doc, never saved ([CONTEXT](../CONTEXT.md)). "Show hidden" lists hidden rows dimmed; next/previous skip hidden entries; hidden lanes undrawn |
| WS-09 | Bookmarks and reviewed marks; Bookmarks view; open at frame | M | todo |  |  |
| WS-10 | Bookmarks to CSV | S | todo |  |  |
| WS-11 | Sidebar views Workspace, Live, Offline, Bookmarks, Plugins; search, sort, filter | M | in progress | `shell/Shell.test.tsx`, `shell/WorkspaceTree.test.tsx` | The Workspace view is a tree of items, entries and lanes from the backend's rows. Other views and search fields are placeholders; no sort or filter |
| WS-12 | Start panel and welcome area | M | in progress | `shell/Shell.test.tsx` | Add Video and Add Playlist work; workspace open, live intake and recent workspaces remain |
| WS-13 | Split viewer area: drag to split, preview vs pinned panes | M | todo |  |  |
| WS-14 | Long playlists load collapsed | S | todo |  |  |
| WS-15 | Hover cards with thumbnails | S | todo |  |  |

## Adding content

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| AC-01 | Add Video with any number of overlays; decoder picked by extension | M | in progress | `crates/workspace/src/intake.rs`, `src/cli/tests.rs`, `tests/{playback_ws,local_video}.rs`, `desktop/args.test.cjs`, `frontend/src/shell/Shell.test.tsx` | `local` and the intake take any number of overlays, each with its decoder. File > Add Video (a remote window asks for a server path) takes none; the native dialog is untested |
| AC-02 | Drop video files on the window | M | todo |  |  |
| AC-03 | Add Live Stream with analytics discovery | M | todo |  |  |
| AC-04 | Add Playlist through a resolver form | M | done | `frontend/src/{shell/AddPlaylistDialog,resolvers/*}.test.*`, `crates/playlist-resolvers/tests/schema.rs`, `src/routes/handlers.rs`, `src/cli/tests.rs`, `tests/{local_video,mot_playlist}.rs`, `make tests-desktop` | The form and `playlist <resolver>` come from the resolver's declared schema; scan is cancellable. It resolves in the background; a failure shows on its row, and only the intake's refusal stays in the dialog. The CLI playlist waits for a local window |
| AC-05 | One intake validation for dialogs, CLI and drops | M | in progress | `crates/workspace/src/intake.rs`, `tests/{local_video,mot_playlist}.rs` | The dialogs and CLI use `workspace::intake`; drops are not built |

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
| OF-01 | Play/pause, step, speed, seek; clock-following playback | M | in progress | `frontend/src/playback/{clock,direction,session,FrameBuffer,protocol,wire,Transport,VideoViewer}.test.*`, `tests/playback_ws.rs`, `crates/foundation` time tests, `crates/media/tests/decode.rs`, `make tests-desktop` | Exact-frame entry (readout not editable) and reverse play are missing. Image sequences (`crates/media/tests/sequence.rs`), variable frame rate and frame errors share the path. Real GPU: D0 spike only |
| OF-02 | Comparison lanes: several overlays on one video; a playlist entry switches the video, and two recordings side by side are two viewers with no shared clock (out of scope) | M | in progress | `tests/{mot_playlist,playback_lanes}.rs`, `crates/overlays/src/tests/`, `crates/workspace/src/intake.rs`, `frontend/src/playback/{LaneGrid,lanes,session,frameSource}.test.*` | The frontend draws each lane from Sample Items and its Frame Map (D22–D27); one overlay file can be several lanes (D24) |
| OF-03 | Playlists of entries and lanes; next/previous entry | M | done | `crates/playlist-resolvers/tests/`, `tests/mot_playlist.rs`, `frontend/src/viewer/{PlaylistViewer,PlaylistFollower}.test.tsx`, `crates/sessions/tests/session/revocation.rs`, `make tests-desktop` | MOT Challenge and folder-pair playlists, a lane per overlay file. A following window moves with the presenter's entry |
| OF-04 | Timestamp matching, sequence-keyed files, sticky overlays | M | in progress | `crates/overlay-sync/src/select/tests.rs`, `crates/overlays/src/tests/delivery.rs`, `tests/mot_playlist.rs` | Per lane, session only ([invariants](domain/invariants.md#frame-identity)); Frame Maps carry the choice to the viewer (D26) |
| OF-05 | Timing and alignment diagnostics per lane | M | in progress | `crates/overlay-sync/src/alignment.rs`, `crates/overlays/src/tests/statuses.rs` | A lane warns when its samples end far from the video or under 80% sit on a frame; unlike the reference, a few unmatched samples are no warning. Left: video cadence |
| OF-06 | Decoded-frame cache under one memory allowance | M | in progress | `crates/playback/src/tests/`, `tests/playback_ws.rs`, `src/video_cache.rs`, `crates/settings` tests | Allowance, equal shares, eviction, live resize and uncached oversized frames work. Left: cache marks (M4) |
| OF-07 | Prepare next playlist entry in the background | S | todo |  |  |
| OF-08 | Export with overlays: lane selection, quality presets, cancellable | M | in progress | `crates/{export,media}/tests/`, `tests/export.rs`, `src/render_host/`, `frontend/src/{export,render}/`, `make tests-desktop` | New: PNG of one frame. [Speed](../crates/export/README.md#measured): `make bench-export` |

### Deviations from the reference

The offline decisions taken with the owner are in
[14](rebuild/14-build-order.md#decisions-taken-for-offline-video). The rest:

| Row | Deviation | Reason |
|-----|-----------|--------|
| OF-02 | Lanes share one video; the reference let a secondary lane have its own video, length and frame rate, aligned to the primary's frame index | D19: two recordings side by side are two viewers |
| RC-01 | Catalog schema v4 replaces v3: rules plus ordered layers (replacing the fixed order), label placement lists, one expression language, no `extends` and no `id` (a user catalog is its file). The reference's catalogs do not load | D25: the frontend evaluates catalogs per lane; D33: location is identity |
| OF-06 | The frame cache lives in `playback`, not `media` | Eviction follows playback windows; export, thumbnails and live skip it |
| OF-06 | Eviction takes the frame farthest from the playhead outside the protected windows; the reference used LRU | It suits scrubbing: after a jump, frames around the old position are the least wanted |
| OF-06 | Auto is never below 256 MiB; the reference had no floor | The setting's smallest value; a low-memory machine still gets a cache |
| OF-01 | The index is built by scanning packets; the reference decoded the whole video. The decode scan is the fallback | Spike: 1.1 s against 36 s for 10 minutes of 1080p30, same results |
| OF-01 | The index is valid by a stat fingerprint plus a digest of the first and last 64 KiB; the reference used the stat fingerprint alone | Hashing a huge video defeats instant reopen |
| OF-01 | Walking back through a group longer than the budget decodes a block of half the budget at a time | No keyframe group is decoded twice to keep one more frame |
| OF-01 | The client ends the session when the protocol version in `Opened` differs from its own | A stale window stops with a message, not misparsed frames |
| OF-01 | Playback commands sit in a View > Playback submenu; the keys, including Left and Right for 10 frames, are the reference's | There is no Playback menu |
| ST-01 | `storage.cache_dir` and `storage.catalog_dir` take no `$NAME` reference; [08](rebuild/08-settings-cli.md) asked for references in storage fields | Browse yields an absolute path; a reference would have to resolve before the first window opens |

## Inspection and display

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| IN-01 | Media tools panel per lane with Objects and Events lists, object and event pages | M | in progress | `frontend/src/{playback/{Inspector,inspection},tools/ObjectsPanel}.test.*`, `tests/playback_inspect.rs`, `make tests-desktop` | Objects list (one lane at a time) and the selected object's fields, Debug ones with origin (D36). Left: Events list, object and event pages |
| IN-02 | Object filters by type and score/confidence range; whole-file filtering with declared policies | M | in progress | `crates/scene/src/filter.rs`, `frontend/src/present/filter/filter.test.ts`, `frontend/src/playback/LaneMenu.test.tsx`, `crates/scene-store/src/tests/history.rs`, `tests/mot_playlist.rs` | Class toggles apply in both scopes; File scope judges every recorded class (D30). The filter expression (`score >= 0.4`) filters the frame only |
| IN-03 | Hover chip and click selection; can be turned off | M | done | `frontend/src/present/pick/pick.test.ts`, `frontend/src/playback/VideoViewer.objects.test.tsx` | Smallest box in the lane's filtered rows; kept by id across seeks, shown in its source's lanes. `overlays.object_details` turns it off (D36) |
| IN-04 | Frame info HUD, lane names | M | in progress | `frontend/src/playback/VideoViewer.test.tsx`, `make tests-desktop` | Lane names are on the tags. Left: the frame info HUD |
| IN-05 | Zoom, pan, reset, synced across lanes; lane and app fullscreen | M | in progress | `frontend/src/playback/{viewport,uiZoom,VideoCanvas}.test.*` | One viewport per viewer, shared by its lanes (D31); in Electron, overlay pixel lengths ignore page zoom (D32). Left: fullscreen |
| IN-06 | Per-view overlay detail visibility | M | done | `frontend/src/playback/LaneMenu.test.tsx`, `frontend/src/present/pick/pick.test.ts` | Features off per lane; hidden objects keep hover and inspection |
| IN-07 | Per-view sticky/sync/filter settings, Apply to all, defaults for new views | S | in progress | `frontend/src/playback/{LaneMenu,lanes,sessionLanes}.test.*` | Sticky, sync, filters, theme and features per lane, kept by position across entries; Apply to all copies them. A policy change blanks the lane until new maps arrive (D35). Left: defaults for new views |
| IN-08 | Structured query search in Objects and Events lists | M | in progress | `frontend/src/search/query.test.ts`, `crates/scene-store/src/tests/query.rs` | Both scopes share the parser and `fixtures/search`. Frame scope searches id, class, motion, score and confidence only. Left: Events list |
| IN-09 | Frame vs File scope for objects | M | done | `frontend/src/tools/ObjectsPanel.test.tsx`, `make tests-desktop` | File scope follows the lane's class toggles, not its frame filter; not for a followed window. Selecting a dimmed row goes to its first frame |
| IN-10 | Pinned fields kept across object pages | S | todo |  |  |
| IN-11 | Copy a field or section value | S | todo |  |  |
| IN-12 | Presence strip with seeking; previous/next appearance | M | todo |  |  |
| IN-13 | Event to frame (offline; live events have no seekable history); event/object cross-navigation | M | todo |  |  |

## Render catalogs

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| RC-01 | Catalog language (schema v4) and the six built-ins | M | done | `frontend/src/present/{oracle.test.ts,catalog/{catalog,schema}.test.ts}`, `crates/catalogs` tests | All six match the reference on the oracle sheets. The editors' JSON Schema is generated from the compiler's tables |
| RC-02 | Per-view selection, Apply to all, Use as default, hot reload | M | done | `frontend/src/playback/{LaneMenu,laneCatalog,catalogs}.test.*`, `crates/catalogs` tests, `tests/playback_catalogs.rs` | Built-in and `user:<file>` catalogs, hot-reloaded; a failing one keeps its last good one. Default: `overlays.default_catalog` |
| RC-03 | Catalog viewer window with seeded sheets, New copy, Delete | M | todo |  |  |
| RC-04 | `catalog list/check/render/new/use/reference` | M | in progress | `src/cli/tests.rs`, `tests/catalog_render.rs` | `catalog list`; `catalog check` (frontend compiler, Electron-as-Node) of a file or `user:<file>`; `catalog render` (overview sheet); others not built |

## Plugins

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| PL-01 | Decoder plugins (file, payload) and playlist resolver plugins | M | in progress | `crates/converters/tests/`, `tests/converters.rs` | Converters ([plugins](plugins.md), D29). Left: payload decoders, plugin resolvers, `local --overlay-option` |
| PL-02 | Built-in decoders and resolvers listed by `list-handlers` and `playlist --help` | M | in progress | `src/cli/tests.rs`, `src/routes/handlers.rs`, `crates/handlers` tests | MOT, UVG-VCM, ONVIF XML, CVAT and converters; both resolvers. ADF beta/v1 frame, consolidated and payload decoders remain. CLI help is generated, untested |
| PL-03 | Install, update, remove, list from CLI and Plugins view | M | in progress | `tests/converters.rs`, `src/cli/tests.rs` | `plugins install/remove/list/check`; applied on restart. Left: `update`, Plugins view |
| PL-04 | Load failures never stop the app and are listed | M | in progress | `tests/converters.rs` | `plugins list` shows load failures; a failed run leaves its lane Incomplete. Left: Plugins view |
| PL-05 | Unchanged third-party Python plugins run (bridge); not planned by default | S | todo |  |  |

## Settings and application

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| ST-01 | Settings dialog pages; restart marker | M | in progress | `frontend/src/shell/SettingsDialog.test.tsx`, `Shell.test.tsx` | Generated from the settings table (pages, sections, restart marker and banner, Browse fields); page-less rows stay out. Left: the other settings (M4/M5) |
| ST-02 | Quick Setup on first start | M | done | `frontend/src/shell/QuickSetupDialog.test.tsx`, `Shell.test.tsx` | Built from the `quick_setup` rows (the theme; the camera protocol joins in M5); opens on first run and from Help; each pick is stored live |
| ST-03 | Theme (light, dark, auto), applied live; no text-size setting, zoom is the browser's | M | done | `crates/settings` tests, `App.test.tsx` | Stored through `/api/settings`; applied live by the frontend |
| ST-04 | `$VARIABLE` references | M | in progress | `crates/settings` tests | Backend done: rows with `env` store the reference and expand it on use; none takes one |
| ST-05 | Keyboard shortcuts, dialog, override persistence | M | done | `crates/settings` tests, `frontend/src/shell/ShortcutsDialog.test.tsx`, `Shell.test.tsx` | Command table, override-only storage, conflict and reserved-key checks, the Shortcuts dialog, live key handling; actions follow each feature row |
| ST-06 | Workspace credential references expand only for their bound host or a confirmed one; warn before saving a literal password (new) | M | in progress | `frontend/src/shell/SettingsDialog.test.tsx` | The Settings dialog warns under a secret field holding a literal (mocked). Left: the first real secret row, workspace half (M5) |
| AP-01 | Uncaught errors logged; continue or close | M | todo |  |  |
| AP-02 | Restart from inside the app | M | todo |  |  |
| AP-03 | `clear-cache` | S | todo |  |  |
| AP-04 | About dialog | S | todo |  |  |
| AP-05 | Custom window frame: in-page title bar with window controls | S | done | `shell/WindowControls.test.tsx` | Frameless; dragging and maximize on a real window manager are untested |
| AP-06 | Open logs folder / copy diagnostics (new) | S | todo |  |  |

## Sharing and remote use (new)

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| SH-01 | Join, reconnect and revoke access | M | in progress | `crates/sessions/tests/session/`, `tests/{session_socket,playback_ws}.rs` | Lifecycle, leases and tickets built; real-network interruption evidence remains |
| SH-02 | Follow or explore independently | M | in progress | `frontend/src/viewer/PlaylistFollower.test.tsx`, `desktop/smoke-sharing.cjs`, `src/routes/playback/inspect/tests.rs` | Video and playlist following and exploring. Inspection is gated like data: a follower outside its window gets none. Live policies follow live |
| SH-03 | Audience privacy and settings scope | M | in progress | `tests/access.rs`, `src/session/project.rs`, `crates/settings/src/scope_tests.rs` | Host-path projections and personal/server settings built; disclosure of inspection data and user catalogs is open |
| SH-04 | Browser and remote Electron connection | M | in progress | `desktop/smoke-remote.cjs`, `frontend/src/session/Notice.test.tsx` | Server frontend and password recovery built; LAN failure feedback, launch delivery to a running app and packaged protocol registration remain |

## Developer tooling

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| DT-01 | Isolated GUI test environment for agents and CI | M | in progress | `make tests-desktop` | `scripts/isolated`: private home and display, user-file guard; Linux |
| DT-02 | UI screenshot harness | M | in progress | `make tests-desktop` | `desktop/smoke*.cjs`: main window, menus, dialogs, the fixture playlist's lanes and Objects list in light and dark, at the window minimum and 200% zoom, and the video and sharing scenarios |
| DT-03 | Rendering benchmarks with A/B against a ref | S | in progress | `make bench` | `overlays` bench of the Sample Item encoder; numbers on stdout, nothing asserted |
| DT-04 | Timestamp alignment report | S | todo |  |  |
| DT-05 | Release check in clean environments | S | todo |  |  |
| DT-06 | Demo recorder | S | todo |  |  |
