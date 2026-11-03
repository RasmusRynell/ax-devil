# Parity

One line per parity row of [01](rebuild/01-product-scope.md), which defines each row and its priority (M needed for parity,
S may follow with owner consent). Status: todo, in progress, done, dropped; dropping an M row needs the
owner's written decision. A new feature gets a row in 01 first.

## Workspace

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| WS-01 | Ordered items (video, live stream, playlist); Untitled until named | M | in progress | `crates/workspace/src/intake.rs`, `crates/workspace-store` tests, `src/session/project.rs`, `tests/{local_video,mot_playlist}.rs` | Video and playlist items, in memory; no live streams, naming or saving (M3) |
| WS-02 | New, Open, Save, Save As, recent workspaces | M | done | `crates/workspace-files` tests, `tests/workspace_files.rs`, `desktop/workspace-dialogs.test.cjs`, `shell/WorkspaceFiles.test.tsx` | Version 2 files; Python files are refused, with no importer |
| WS-03 | No prompts; recovery copy, restore banner, per-instance lock | M | done | `crates/workspace-files` and `crates/workspace-store` tests, `tests/workspace_files.rs`, `shell/WorkspaceFiles.test.tsx` | The lock is per window, an OS lock on a sidecar file. Copies are named after the workspace, with no timestamp, in the XDG state folder (no storage setting) |
| WS-04 | Unreadable items preserved | M | done | `crates/workspace-files` tests | An item with an unknown kind, an unknown key or a malformed body is kept and written back as read |
| WS-05 | Background resolution; pending and unavailable rows | M | in progress | `crates/workspace-store` tests, `crates/playlist-resolvers/tests/mot_challenge.rs`, `frontend/src/{viewer/ViewerHost,shell/WorkspaceTree}.test.tsx`, `tests/local_video.rs` | A pending or failed item has a sidebar row; a failed one keeps its reason, a playlist's skipped sequences are a warning on its row. Left: an unavailable row should not open (invariants) but opens a viewer showing the reason |
| WS-06 | Re-resolve on file change; reload offered to open viewers | M | todo |  |  |
| WS-07 | Rename, Edit, Remove, item information | M | todo |  |  |
| WS-08 | Hide/Unhide entries and lanes | M | done | `crates/workspace-store` tests, `crates/sessions/tests/session/matrix.rs`, `src/session/project.rs`, `frontend/src/{shell/WorkspaceTree,viewer/{PlaylistViewer,hidden},playback/{LaneGrid,VideoViewer}}.test.*` | Kept in the Doc, never saved ([CONTEXT](../CONTEXT.md)). "Show hidden" lists hidden rows dimmed; next/previous skip hidden entries; hidden lanes undrawn |
| WS-09 | Bookmarks and reviewed marks; Bookmarks view; open at frame | M | in progress | `crates/workspace-files` tests | Data model, validation and the file round trip only; no commands, view or CSV |
| WS-10 | Bookmarks to CSV | S | todo |  |  |
| WS-11 | Sidebar views Workspace, Live, Offline, Bookmarks, Plugins; search, sort, filter | M | in progress | `shell/Shell.test.tsx`, `shell/WorkspaceTree.test.tsx` | The Workspace view is a tree of items, entries and lanes from the backend's rows. The Plugins view lists, installs a folder, updates and removes plugins. Other views and search fields are placeholders; no sort or filter |
| WS-12 | Start panel and welcome area | M | in progress | `shell/Shell.test.tsx`, `shell/WorkspaceFiles.test.tsx` | Add Video, Add Playlist, Open Workspace and recent workspaces work; live intake remains |
| WS-13 | Split viewer area: drag to split, preview vs pinned panes | M | todo |  |  |
| WS-14 | Long playlists load collapsed | S | todo |  |  |
| WS-15 | Hover cards with thumbnails | S | todo |  |  |

## Adding content

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| AC-01 | Add Video with any number of overlays; decoder picked by extension | M | in progress | `crates/workspace/src/intake.rs`, `src/cli/tests.rs`, `tests/{playback_ws,local_video}.rs`, `desktop/args.test.cjs`, `frontend/src/shell/Shell.test.tsx` | `local` and the intake take any number of overlays, each with its decoder. `local --overlay-option` and Add Video's one overlay give a decoder its options. A remote window asks for a path; the native dialog is untested |
| AC-02 | Drop video files on the window | M | todo |  |  |
| AC-03 | Add Live Stream with analytics discovery | M | todo |  |  |
| AC-04 | Add Playlist through a resolver form | M | done | `shell/AddPlaylistDialog.test.tsx`, `resolvers/*.test.*`, `crates/handler-api/tests/form.rs`, `src/routes/{handlers,resolve}.rs`, `src/cli/tests.rs`, `tests/{local_video,mot_playlist}.rs`, `make test-desktop` | The dialog resolves first and adds the item only on success; a failure stays in the dialog. A resolver may return several playlists. The CLI playlist waits for a local window and resolves in the background |
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
| OF-01 | Play/pause, step, speed, seek; clock-following playback | M | in progress | `frontend/src/playback/{chromeVisibility,clock,direction,session,FrameBuffer,protocol,wire,Transport,VideoViewer}.test.*`, `tests/playback_ws.rs`, `crates/foundation` time tests, `crates/media/tests/decode.rs`, `make test-desktop` | Reverse play is missing. Frame entry, speed presets and floor, and the scrub hold are proved by `Transport.test.tsx`, `clock.test.ts` and `session.test.ts`. Image sequences (`crates/media/tests/sequence.rs`), variable frame rate and frame errors share it. Real GPU: D0 spike only |
| OF-02 | Comparison lanes: several overlays on one video; a playlist entry switches the video, and two recordings side by side are two viewers with no shared clock (out of scope) | M | in progress | `tests/{mot_playlist,playback_lanes}.rs`, `crates/overlays/src/tests/`, `crates/workspace/src/intake.rs`, `frontend/src/playback/{LaneGrid,lanes,session,frameSource}.test.*` | The frontend draws each lane from Sample Items and its Frame Map (D22–D27); one overlay file can be several lanes (D24) |
| OF-03 | Playlists of entries and lanes; next/previous entry | M | done | `crates/playlist-resolvers/tests/`, `tests/mot_playlist.rs`, `frontend/src/viewer/{PlaylistViewer,PlaylistFollower}.test.tsx`, `crates/sessions/tests/session/revocation.rs`, `make test-desktop` | MOT Challenge and folder-pair playlists, a lane per overlay file. A following window moves with the presenter's entry |
| OF-04 | Timestamp matching, sequence-keyed files, sticky overlays | M | in progress | `crates/overlay-sync/src/select/tests.rs`, `crates/overlays/src/tests/delivery.rs`, `tests/mot_playlist.rs` | Per lane, session only ([invariants](domain/invariants.md#frame-identity)); Frame Maps carry the choice to the viewer (D26) |
| OF-05 | Timing and alignment diagnostics per lane | M | done | `crates/overlay-sync/src/alignment.rs`, `crates/overlays/src/tests/statuses.rs`, `frontend/src/playback/{cadence,LaneMenu}.test.*`, `make test-desktop` | The Timing tab shows the video's cadence and the lane's match; a lane warns when its samples end far from the video or under 80% sit on a frame |
| OF-06 | Decoded-frame cache under one memory allowance | M | in progress | `crates/playback/src/tests/`, `tests/playback_ws.rs`, `src/video_cache.rs`, `crates/settings` tests | Allowance, equal shares, eviction, live resize and uncached oversized frames work. Cached frames are shaded on the seek bar |
| OF-07 | Prepare next playlist entry in the background | S | todo |  |  |
| OF-08 | Export with overlays: lane selection, quality presets, cancellable | M | in progress | `crates/{export,media}/tests/`, `tests/export.rs`, `src/render_host/`, `frontend/src/{export,render}/`, `make test-desktop` | New: PNG of one frame. [Speed](../crates/export/README.md#measured): `make bench-export` |

### Deviations from the reference

Listed in [parity deviations](parity-deviations.md).

## Inspection and display

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| IN-01 | Media tools panel per lane with Objects and Events lists, object and event pages | M | in progress | `frontend/src/{playback/{Inspector,inspection},tools/{ObjectsPanel,panelWidth,table/*}}.test.*`, `tests/playback_inspect.rs`, `make test-desktop` | Objects list and selected object's fields, Debug ones with origin (D36). The panel resizes by its left divider; a row copies as tab-separated text. Left: Events list, object and event pages |
| IN-02 | Object filters by type and score/confidence range; whole-file filtering with declared policies | M | in progress | `crates/scene/src/filter.rs`, `frontend/src/present/filter/filter.test.ts`, `frontend/src/playback/LaneMenu.test.tsx`, `crates/scene-store/src/tests/history.rs`, `tests/mot_playlist.rs` | Class toggles apply in both scopes; File scope judges every recorded class (D30). The filter expression (`score >= 0.4`) filters the frame only |
| IN-03 | Hover chip and click selection; can be turned off | M | done | `frontend/src/present/pick/pick.test.ts`, `frontend/src/playback/{VideoViewer.objects,VideoCanvas,chipPlacement}.test.*` | Smallest box in the lane's filtered rows; kept by id across seeks, shown in its source's lanes. `overlays.object_details` turns it off (D36) |
| IN-04 | Frame info HUD, lane names | M | done | `frontend/src/playback/{hud,VideoViewer.objects}.test.*`, `make test-desktop` | Lane names are on the tags; the HUD (I) covers them while it shows. PTS rows need a time base of a microsecond or coarser |
| IN-05 | Zoom, pan, reset, synced across lanes; lane and app fullscreen | M | done | `frontend/src/playback/{viewport,uiZoom,VideoCanvas,VideoViewer.objects}.test.*`, `desktop/smoke-video.cjs` | One viewport per viewer, shared by its lanes (D31); in Electron, overlay pixel lengths ignore page zoom (D32). Lane fullscreen is the browser Fullscreen API on the viewer pane; Esc leaves it and the app fullscreen (F11) stays. |
| IN-06 | Per-view overlay detail visibility | M | done | `frontend/src/playback/LaneMenu.test.tsx`, `frontend/src/present/pick/pick.test.ts` | Features off per lane; hidden objects keep hover and inspection |
| IN-07 | Per-view sticky/sync/filter settings, Apply to all, defaults for new views | S | in progress | `frontend/src/playback/{LaneMenu,lanes,sessionLanes}.test.*` | Sticky, sync, filters, theme and features per lane, kept by position across entries; Apply to all copies them. A policy change blanks the lane until new maps arrive (D35). Left: defaults for new views |
| IN-08 | Structured query search in Objects and Events lists | M | in progress | `frontend/src/search/query.test.ts`, `crates/scene-store/src/tests/query.rs` | Searches Frame id, class, motion.state, score, confidence; File id, class, event; no others. Left: Events list |
| IN-09 | Frame vs File scope for objects | M | done | `frontend/src/tools/ObjectsPanel.test.tsx`, `make test-desktop` | File scope follows the lane's class toggles, not its frame filter; not for a followed window. A pick outside the frame seeks its first frame |
| IN-10 | Pinned fields kept across object pages | S | done | `playback/{fields,Inspector}.test.*` |  |
| IN-11 | Copy a field or section value | S | done | `playback/{fields,Inspector}.test.*` |  |
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
| PL-01 | Decoder plugins (file, payload) and playlist resolver plugins | M | in progress | `crates/plugins/tests/`, `tests/plugins.rs` | Plugins with decoder and playlist resolver handlers ([plugins](plugins.md), D29). Left: payload and live decoders, which wait for live sources |
| PL-02 | Built-in decoders and resolvers listed by `list-handlers` and `playlist --help` | M | in progress | `src/cli/tests.rs`, `src/routes/handlers.rs`, `crates/handlers` tests, `crates/scene-decoders/tests/adf_*.rs` | MOT, UVG-VCM, ONVIF XML, CVAT, ADF beta/v1 (frame and consolidated) and plugin decoders; both resolvers. ADF as live payload decoders waits for live sources. CLI help is generated, untested |
| PL-03 | Install, update, remove, list from CLI and Plugins view | M | done | `tests/plugins.rs`, `src/cli/tests.rs`, `src/routes/plugins.rs`, `frontend/src/plugins/PluginsView.test.tsx` | `plugins install/update/remove/list/check` from the CLI; the Plugins view lists, installs a folder, updates (reads the manifest again) and removes; applied to the running app. An edited manifest is also picked up by itself |
| PL-04 | Load failures never stop the app and are listed | M | done | `tests/plugins.rs`, `frontend/src/plugins/PluginsView.test.tsx` | `plugins list` and the Plugins view show load failures with their reason; a failed run leaves its lane Incomplete |
| PL-05 | Unchanged third-party Python plugins run (bridge); not planned by default | S | todo |  |  |

## Settings and application

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| ST-01 | Settings dialog pages; restart marker | M | in progress | `frontend/src/shell/SettingsDialog.test.tsx`, `Shell.test.tsx` | Generated from the settings table (pages, sections, restart marker, Browse fields); page-less rows stay out. Left: the other settings (M4/M5) |
| ST-02 | Quick Setup on first start | M | done | `frontend/src/shell/QuickSetupDialog.test.tsx`, `Shell.test.tsx` | Opens on first run and from Help; any close stores the choices and marks setup done; a refused write does not |
| ST-03 | Theme (light, dark, auto) and text size (system, small to larger), applied live | M | done | `crates/settings` tests, `App.test.tsx`, `settings.test.tsx` | Stored through `/api/settings`; applied live; zoom stays the browser's |
| ST-04 | `$VARIABLE` references | M | in progress | `crates/settings` tests | Backend done: rows with `env` store the reference and expand it on use; none takes one |
| ST-05 | Keyboard shortcuts, dialog, override persistence | M | done | `crates/settings` tests, `frontend/src/shell/ShortcutsDialog.test.tsx`, `Shell.test.tsx` | Command table, override-only storage, conflict and reserved-key checks, the Shortcuts dialog, live key handling; actions follow each feature row |
| ST-06 | Workspace credential references expand only for their bound host or a confirmed one; warn before saving a literal password (new) | M | in progress | `frontend/src/shell/SettingsDialog.test.tsx` | The Settings dialog warns under a secret field holding a literal (mocked). Left: the first real secret row, workspace half (M5) |
| AP-01 | Uncaught errors logged; continue or close | M | done | `UncaughtErrors.test.tsx`, `desktop/uncaught.test.cjs` | The page reports its errors to the backend's log |
| AP-02 | Restart from inside the app | M | done | `frontend/src/shell/Shell.test.tsx`, `desktop/args.test.cjs` | A setting that needs a restart offers to relaunch the desktop app on its own backend; a browser or remote window is only told |
| AP-03 | `clear-cache` | S | done | `src/disk_cache.rs` test | Lists, asks, removes, counts what it removed; only ax-devil's own files in its cache subfolders |
| AP-04 | About dialog | S | done | `shell/Shell.test.tsx` |  |
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
| DT-01 | Isolated GUI test environment for agents and CI | M | in progress | `make test-desktop` | `scripts/isolated`: private home and display, user-file guard; Linux |
| DT-02 | UI screenshot harness | M | in progress | `make test-desktop` | `desktop/smoke*.cjs`: main window, menus, dialogs, the fixture playlist's lanes and Objects list in light and dark, at the window minimum and 200% zoom, and the video and sharing scenarios |
| DT-03 | Rendering benchmarks with A/B against a ref | S | in progress | `make bench` | `overlays` bench of the Sample Item encoder; numbers on stdout, nothing asserted |
| DT-04 | Timestamp alignment report | S | todo |  |  |
| DT-05 | Release check in clean environments | S | todo |  |  |
| DT-06 | Demo recorder | S | todo |  |  |
