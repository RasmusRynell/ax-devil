# Parity

One line per parity row of [01](rebuild/01-product-scope.md), which defines each row and its priority. Status: todo,
in progress, done, dropped; dropping an M row needs the
owner's written decision. A new feature gets a row in 01 first.

## Workspace

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| WS-01 | Ordered items (video, live, playlist); Untitled until named | M | done | `crates/workspace/src/intake.rs`, `crates/workspace-store` tests, `src/session/project.rs`, `tests/it/{local_video,mot_playlist,workspace_files}.rs`, `kit/EditableLabel.test.tsx` | Rename in the sidebar title |
| WS-02 | New, Open, Save, Save As, recent workspaces | M | done | `crates/workspace-files` tests, `tests/it/workspace_files.rs`, `desktop/workspace-dialogs.test.cjs`, `shell/WorkspaceFiles.test.tsx` | Version 2 files; Python files are refused, with no importer |
| WS-03 | No prompts; recovery copy, restore banner, per-instance lock | M | done | `crates/workspace-files` and `crates/workspace-store` tests, `tests/it/workspace_files.rs`, `shell/WorkspaceFiles.test.tsx` | The lock is per window, an OS lock on a sidecar file. Copies are named after the workspace, with no timestamp |
| WS-04 | Unreadable items preserved | M | done | `crates/workspace-files` tests | An item with an unknown kind or key, or a malformed body, is kept and written back as read |
| WS-05 | Background resolution; pending and unavailable rows | M | done | `crates/workspace-store` tests, `crates/playlist-resolvers/tests`, `shell/WorkspaceTree.test.tsx`, `tests/it/local_video.rs` | A pending or failed item has a sidebar row; a failed one keeps its reason; skipped sequences warn. An unavailable row's Open shows the reason only |
| WS-06 | Re-resolve on file change; reload offered to open viewers | M | done | `crates/sessions` tests, `SplitArea.test.tsx` |  |
| WS-07 | Rename, Edit, Remove, item information | M | done | `crates/workspace-store` tests, `src/session` tests, `shell/{WorkspaceTree,Add*Dialog}.test.tsx` | Rename is the Edit form's Name; a new recipe resolves again, keeping place and marks |
| WS-08 | Hide/Unhide entries and lanes | M | done | `crates/workspace-store` tests, `crates/sessions/tests/session/matrix.rs`, `src/session/project.rs`, `{shell/WorkspaceTree,viewer/{PlaylistViewer,hidden},playback/{LaneGrid,VideoViewer}}.test.*` | Kept in the Doc, never saved ([CONTEXT](../CONTEXT.md)). Show hidden lists them dimmed; next/previous skip them; hidden lanes undrawn |
| WS-09 | Bookmarks and reviewed marks; Bookmarks view; open at frame | M | done | `workspace-store/src/bookmarks.rs`, `tests/it/bookmarks.rs`, `bookmarks/` |  |
| WS-10 | Bookmarks to CSV | S | done | `tests/it/bookmarks.rs`, `make test-desktop` |  |
| WS-11 | Sidebar views Workspace, Live, Offline, Bookmarks, Plugins; search, sort, filter | M | done | `shell/{Sidebar*,WorkspaceTree*,OfflineAdd,ActivityBar,treeModel}.test.*`, `live/rows.test.ts` | Search, Sort, folds and width outlive a view switch; viewers reveal their item. Same-named rows (LOB-014) and cameras get a detail line: place, stream state. Offline has Add; found cameras match held hosts, expanded; Plugins flags failures. Offline sorts by Location |
| WS-12 | Start panel and welcome area | M | done | `shell/{Shell,WorkspaceFiles,Sidebar}.test.tsx` | Only while empty; heading only with recents; Open Workspace default; Live: AC-03 |
| WS-13 | Split viewer area: drag to split, preview vs pinned panes | M | done | `viewer/*.test.*`, `playback/{budget,videoDetails}.test.ts`, `desktop/smoke-video.cjs` header details | Header menu: the row's, without Edit |
| WS-14 | Long playlists load collapsed | S | done | `shell/reveal.test.ts` |  |
| WS-15 | Hover cards with thumbnails | S | in progress | `tests/it/{local_video,glance}.rs`, `shell/{WorkspaceTree*,LiveView}.test.tsx` | Offline, Live |

## Adding content

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| AC-01 | Add Video with any number of overlays; decoder picked by extension | M | done | `crates/workspace/src/intake.rs`, `src/{cli/tests,routes/intake}.rs`, `tests/it/{playback_ws,local_video}.rs`, `desktop/args.test.cjs`, `shell/{Shell.addVideo,AddVideoDialog}.test.tsx`, `crates/handlers` tests | A remote window asks for a path; an unreadable file keeps Add off |
| AC-02 | Drop video files on the window | M | dropped |  | See [deviations](parity-deviations.md) |
| AC-03 | Add Live Stream with analytics discovery | M | done | `shell/AddLiveStreamDialog.test.tsx`, `src/session/reader/tests.rs` | Edit keeps the saved login on the same camera |
| AC-04 | Add Playlist through a resolver form | M | done | `shell/{AddPlaylistDialog,WorkspaceTree}.test.tsx`, `resolvers/*.test.*`, `crates/handler-api/tests/form.rs`, `src/routes/{handlers,resolve,scan}.rs`, `src/cli/tests.rs`, `tests/it/{local_video,mot_playlist}.rs`, `make test-desktop` | Resolves before adding, so a failure stays in the dialog; a resolver may return several. The CLI playlist waits for a local window |
| AC-05 | One intake validation for dialogs and the CLI | M | done | `crates/workspace/src/intake.rs`, `src/cli/tests.rs`, `tests/it/{local_video,mot_playlist}.rs` | Dialogs and CLI use `workspace::intake` |

## Live

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| LV-01 | RTSP video with one overlay: embedded ONVIF metadata, MQTT or DataHub | M | done | `camera/tests/it/{rtsp,mqtt,datahub}.rs`, `live_sources` | |
| LV-02 | Per-feed status: connected, reconnecting, error, stalled; Retry | M | done | `live_sources`, `live/{words,session}.test.ts` | |
| LV-03 | Pause keeps the connection; stop is terminal | M | done | `live_sources`, `live/session.test.ts` | |
| LV-04 | Capture-time synchronization of frames and overlays | M | done | `overlay-sync`, `live_sources` | |
| LV-05 | Network scan with optional sign-in; add found camera | M | done | `camera/tests/it/scan.rs`, `LiveView.test.tsx` | |
| LV-06 | Streams start only on explicit action | M | done | `workspace-store` tests, `sessions` streams, `LiveView.test.tsx` | |
| LV-07 | Live Events list keeps recording while hidden; no seekable history | M | done | `live/session.test.ts` | |
| LV-08 | Temporary device configuration (MQTT analytics publisher) removed on stop and quit | M | done | `camera/tests/it/mqtt.rs`, `live_sources` | |

## Offline playback and comparison

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| OF-01 | Play/pause, step, speed, seek; clock-following playback | M | done | `playback/{clock,direction,session,FrameBuffer,protocol,wire,Transport,VideoViewer}.test.*`, `tests/it/playback_ws.rs`, `crates/foundation` time tests, `crates/media/tests/it/{decode,sequence}.rs`, `make test-desktop` | Image sequences and variable frame rate share it. An open shows a loading cover; a decode failure stops. Real GPU: D0 spike only |
| OF-02 | Comparison lanes: several overlays on one video; a playlist entry opens at its start, and two recordings side by side are two viewers with no shared clock | M | done | `tests/it/{mot_playlist,playback_lanes}.rs`, `crates/overlays/src/tests/`, `playback/{LaneGrid,inspection,lanes,session,frameSource}.test.*`, `desktop/smoke-playlist.cjs` | The frontend draws a lane from Sample Items and its Frame Map (D22–D27); one overlay file can be several lanes (D24) |
| OF-03 | Playlists of entries and lanes; next/previous entry | M | done | `crates/playlist-resolvers/tests/`, `tests/it/mot_playlist.rs`, `viewer/{PlaylistViewer,PlaylistFollower}.test.tsx`, `crates/sessions/tests/session/revocation.rs`, `make test-desktop` | MOT Challenge and folder-pair playlists, a lane per overlay file. A following window follows the presenter's entry |
| OF-04 | Timestamp matching, sequence-keyed files, sticky overlays | M | done | `crates/overlay-sync/src/select/tests.rs`, `crates/overlays/src/tests/{delivery,statuses}.rs`, `tests/it/mot_playlist.rs`, `playback/{LaneMenuTiming,lanes}.test.*` | Per lane, session only ([invariants](domain/invariants.md#frame-identity)); Frame Maps carry the choice to the viewer (D26) |
| OF-05 | Timing and alignment diagnostics per lane | M | done | `crates/media/src/index/cadence.rs`, `crates/media/tests/it/{decode,sequence}.rs`, `crates/overlay-sync/src/alignment.rs`, `crates/overlays/src/tests/statuses.rs`, `playback/LaneMenuTiming.test.tsx`, `make test-desktop` | Cadence and lane match; warns when samples end far or any sit on no frame. Image sequences share it |
| OF-06 | Decoded-frame cache under one memory allowance | M | done | `crates/playback/src/tests/`, `tests/it/playback_ws.rs`, `src/video_cache.rs`, `crates/settings` tests | Allowance, equal shares, eviction, live resize, uncached oversized frames work. Cached frames are shaded on the seek bar |
| OF-07 | Prepare next playlist entry in the background | S | done | `tests/it/prepare_next.rs` | `playback.prepare_next_video` |
| OF-08 | Export with overlays: lane selection, quality presets, cancellable | M | done | `crates/{export,media}/`, `tests/export/export.rs`, `src/render_host/`, `{export,render}/`, `make test-desktop` | New: PNG of one frame. [Speed](../crates/export/README.md#measured): `make bench-export`. Hard links refused on every platform |

### Deviations from the reference

Listed in [parity deviations](parity-deviations.md).

## Inspection and display

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| IN-01 | Media tools panel per lane: Objects and Events lists, object and event pages | M | done | `crates/scene/src/inspection/tests.rs`, `{playback/{inspection,Inspector,fileEvents},tools/**}.test.*`, `tests/it/playback_inspect.rs`, `make test-desktop` | ID dots by class, fields, Debug origin (D36). A page lists 200 events at most |
| IN-02 | Object filters by type and score/confidence range; whole-file filtering with declared policies | M | done | `crates/scene/src/filter.rs`, `{present/filter/filter,playback/LaneMenuFilter}.test.*`, `crates/scene-store/src/tests/history.rs`, `tests/it/mot_playlist.rs` | Class toggles apply in both scopes; File judges every recorded class (D30). The expression and ranges are frame-only (MTP-010) |
| IN-03 | Hover chip and click selection; can be turned off | M | done | `present/pick/pick.test.ts`, `playback/{VideoViewer.objects,VideoCanvas,chipPlacement,ObjectLayer}.test.*` | Smallest box in the lane's filtered rows; kept by id across seeks, shown in its source's lanes. `overlays.object_details` turns it off (D36) |
| IN-04 | Frame info HUD, lane names | M | done | `playback/{hud,VideoViewer.objects}.test.*`, `make test-desktop` | The HUD (I) covers the lane-name tags. PTS rows need a microsecond or coarser time base |
| IN-05 | Zoom, pan, reset, synced across lanes; lane and app fullscreen | M | done | `playback/{viewport,uiZoom,VideoCanvas,VideoViewer.objects}.test.*`, `desktop/smoke-video.cjs` | One viewport per viewer, shared by its lanes (D31); in Electron, overlay pixel lengths ignore page zoom (D32). Lane fullscreen is the Fullscreen API on the viewer; Esc leaves it, F11 stays |
| IN-06 | Per-view overlay detail visibility | M | done | `{playback/LaneMenu,present/pick/pick}.test.*` | Hidden objects keep hover and inspection |
| IN-07 | Per-view sticky/sync/filter settings, Apply to all, defaults for new views | S | done | `playback/{LaneMenu,lanes,sessionLanes}.test.*`, `crates/settings` | Kept by position; Apply to all copies them; Make default saves `overlays.default_{sticky,sync,object_filter}`. |
| IN-08 | Structured query search in Objects and Events lists | M | done | `search/query.test.ts`, `playback/frameFields.test.ts`, `src/routes/playback/inspect/tests.rs`, `crates/scene-store/src/tests/{query,events}.rs`, `tools/MediaToolsPanel*.test.tsx` | Frame: id, class, inspected leaves. File: id, class, event. Events: kind, object |
| IN-09 | Frame vs File scope for objects | M | done | `{tools/{MediaToolsPanel,table/Table},playback/fileScope}.test.*` | File scope follows the lane's class toggles, not its frame filter; not for a followed window. The cursor keeps off-page rows. A pick off the frame seeks to its first |
| IN-10 | Pinned fields kept across object pages | S | done | `playback/{fields,Inspector}.test.*` | |
| IN-11 | Copy a field or section value | S | done | `playback/{fields,Inspector}.test.*` |  |
| IN-12 | Presence strip with seeking; previous/next appearance | M | done | `{playback/{appearanceJumps,Inspector},tools/PresenceStrip}.test.*` | Strip in rows and page, event ticks |
| IN-13 | Event to frame (offline; live events have no seekable history); event/object cross-navigation | M | done | `scene-store/src/tests/events.rs`, `tools/MediaToolsPanel.events.test.tsx` | Pages by index ([deviations](parity-deviations.md)) |

## Render catalogs

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| RC-01 | Catalog language (schema v4) and the six built-ins | M | done | `present/{oracle.test.ts,catalog,layout,text}`, `crates/catalogs` tests | All six match the reference oracle sheets; JSON Schema comes from compiler tables |
| RC-02 | Per-view selection, Apply to all, Use as default, hot reload | M | done | `playback/{LaneMenu,laneCatalog,catalogs}.test.*`, `crates/catalogs` tests, `tests/it/playback_catalogs.rs` | Built-in and `user:<file>`, hot-reloaded or Reload; a failing one keeps its last good. Default: `overlays.default_catalog`; no Other-views line (deviations) |
| RC-03 | Catalog Copy and Delete | M | done | `src/routes/catalogs.rs`, `playback/LaneCatalogActions.test.tsx` | Viewer window and preview sheets dropped by owner decision |
| RC-04 | `catalog` CLI | M | done | `cli/catalog_tests.rs`, `tests/export/catalog_check.rs` | `reference` generated; `render` dropped by owner decision |

## Plugins

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| PL-01 | Decoder plugins (file, payload) and playlist resolver plugins | M | done | `crates/plugins/tests/`, `tests/it/plugins.rs` | Decoder handlers read a file or, with `input: records`, one record at a time (live sources, lines of a file); resolver handlers ([plugins](plugins.md), D29) |
| PL-02 | Built-in decoders and resolvers listed by `list-handlers` and `playlist --help` | M | done | `src/cli/tests.rs`, `src/routes/handlers.rs`, `crates/handlers` tests, `crates/scene-decoders/tests/it/adf_*.rs` | MOT, UVG-VCM, ONVIF XML, CVAT, ADF beta/v1 (frame and consolidated), plugin decoders; both resolvers |
| PL-03 | Install, update, remove, list from CLI and Plugins view | M | done | `tests/it/plugins.rs`, `src/cli/tests.rs`, `src/routes/plugins.rs`, `plugins/PluginsView.test.tsx` | `plugins install/update/remove/list/check` from the CLI and the Plugins view; applied to the running app |
| PL-04 | Load failures never stop the app; listed | M | done | `tests/it/plugins.rs`, `PluginsView.test.tsx` | `plugins list` and Plugins view show failures; a failed run marks its lane Incomplete |
| PL-05 | Unchanged third-party Python plugins run (bridge); not planned by default | S | todo |  |  |

## Settings and application

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| ST-01 | Settings pages; restart marker | M | done | `SettingsDialog{,.table}.test.tsx`, `Shell.test.tsx`, `src/tests.rs` | All pages; storage folders read once at start |
| ST-02 | Quick Setup on first start | M | done | `shell/QuickSetupDialog.test.tsx`, `Shell.test.tsx` | Opens on first run and from Help; any close stores the choices |
| ST-03 | Theme (light, dark, auto) and text size (system, small to larger), applied live | M | done | `crates/settings` tests, `App.test.tsx`, `settings.test.tsx` | Stored through `/api/settings`; applied live; zoom stays the browser's |
| ST-04 | `$VARIABLE` references | M | done | `reference_tests.rs`, `SettingsDialog.test.tsx`, `AddLiveStreamDialog.test.tsx` | Every field takes one, kept raw; unset gives empty or the default, with a warning; a number or folder that does not resolve is refused on save |
| ST-05 | Keyboard shortcuts and overrides | M | done | `crates/settings` tests, `ShortcutsDialog.test.tsx`, `SettingsDialog.test.tsx`, `Shell.test.tsx` | Command table, override storage, conflict checks, live keys; editor from General, OK saves, Cancel ignored while saving |
| ST-06 | Warn before saving a literal password (new); workspace references expand silently (D18) | M | done | `shell/{SettingsDialog{,.table},AddLiveStreamDialog}.test.tsx`, `crates/workspace` live tests | Camera and broker passwords mask and warn on a literal, not a `$NAME` |
| AP-01 | Uncaught errors logged; continue or close | M | done | `UncaughtErrors.test.tsx`, `desktop/uncaught.test.cjs` | The page reports its errors to the backend's log |
| AP-02 | Restart from inside the app | M | done | `shell/Shell.test.tsx`, `desktop/args.test.cjs` | A setting that needs a restart offers to relaunch the desktop app on its own backend; other windows are told |
| AP-03 | `clear-cache` | S | done | `src/disk_cache.rs`, `cli/clear_cache.rs` | `--type main/all`; lists, asks, removes; only ax-devil's own cache files |
| AP-04 | About dialog | S | done | `shell/Shell.test.tsx` |  |
| AP-05 | Custom window frame: in-page title bar with window controls | S | done | `shell/WindowControls.test.tsx` | Frameless; dragging and maximize on a real window manager are untested |
| AP-06 | Open logs folder / copy diagnostics (new) | S | done | `shell/Shell.test.tsx` | In About |

## Sharing and remote use (new)

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| SH-01 | Join, reconnect and revoke access | M | done | `crates/sessions/tests/session/`, `tests/it/{session_socket,playback_ws}.rs` | A cut connection (TCP proxy) resumes its seat; a stopped sharing refuses it; silent loss takes 25 s |
| SH-02 | Follow or explore independently | M | done | `crates/sessions/tests/session/{revocation,matrix}.rs`, `viewer/PlaylistFollower.test.tsx`, `desktop/smoke-sharing.cjs`, `src/routes/playback/inspect/tests.rs` | Video and playlist. Inspection is gated like data: a follower outside its window gets none. Live policies follow live |
| SH-03 | Audience privacy and settings scope | M | done | `tests/it/{access,playback_catalogs}.rs`, `src/session/project.rs`, `crates/settings/src/scope_tests.rs` | Host paths and settings scoped; a watcher sees a user catalog once its Session's host viewer asks for it, until it leaves |
| SH-04 | Browser and remote Electron connection | M | done | `desktop/{smoke-remote,smoke-handoff,links.test,remote.test}.cjs`, `tests/it/workspace_files.rs`, `session/Notice.test.tsx` | Server frontend, password recovery, join and hosting failure reasons (`src/share.rs`); a second launch's link, server or command reaches the running app. Two-machine outages and OS `ax-devil://` registration on Windows and macOS were tested by hand by the owner |

## Developer tooling

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| DT-01 | Isolated GUI test environment for agents and CI | M | done | `make test-desktop` | `scripts/isolated`: private home and display, user-file guard; Linux |
| DT-02 | UI screenshot harness | M | done | `make test-desktop`, `desktop/fit-coverage.test.cjs` | `desktop/smoke*.cjs`: windows, menus, dialogs, lanes and Objects in both themes at the window minimum and 200% zoom, the video and sharing scenarios |
| DT-03 | Rendering benchmarks with A/B against a ref | S | in progress | `make bench` | `overlays` bench of the Sample Item encoder; numbers on stdout, nothing asserted |
| DT-04 | Timestamp alignment report | S | todo |  |  |
| DT-05 | Release check in clean environments | S | todo |  |  |
| DT-06 | Demo recorder | S | todo |  |  |
