# Parity: workspace, adding content and live

Row format: [parity](../parity.md). Deviations: [workspace deviations](workspace-deviations.md).

## Workspace

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| WS-01 | Ordered items (video, live, playlist); Untitled until named | M | done | `crates/workspace/src/intake.rs`, `crates/workspace/src/store` tests, `src/session/project.rs`, `tests/it/{local_video,mot_playlist,workspace_files}.rs`, `kit/EditableLabel.test.tsx` | Rename in the sidebar title |
| WS-02 | New, Open, Save, Save As, recent workspaces | M | done | `crates/workspace-files` tests, `tests/it/workspace_files.rs`, `desktop/workspace-dialogs.test.cjs`, `shell/WorkspaceFiles.test.tsx` | Version 2 files; Python files are refused, with no importer |
| WS-03 | No prompts; recovery copy, restore banner, per-instance lock | M | done | `crates/workspace-files` and `crates/workspace/src/store` tests, `tests/it/workspace_files.rs`, `shell/WorkspaceFiles.test.tsx` | The lock is per window, an OS lock on a sidecar file. Copies are named after the workspace, with no timestamp |
| WS-04 | Unreadable items preserved | M | done | `crates/workspace-files` tests | An item with an unknown kind or key, or a malformed body, is kept and written back as read |
| WS-05 | Background resolution; pending and unavailable rows | M | done | `crates/workspace/src/store` tests, `crates/handlers/tests/resolvers`, `shell/WorkspaceTree.test.tsx`, `tests/it/local_video.rs` | A pending or failed item has a sidebar row; a failed one keeps its reason; skipped sequences warn. An unavailable row's Open shows the reason only |
| WS-06 | Re-resolve on file change; reload offered to open viewers | M | done | `crates/sessions` tests, `shell/SplitArea.test.tsx` |  |
| WS-07 | Rename, Edit, Remove, item information | M | done | `crates/workspace/src/store` tests, `src/session` tests, `shell/{WorkspaceTree,Add*Dialog}.test.tsx` | Rename is the Edit form's Name; a new recipe resolves again, keeping place and marks |
| WS-08 | Hide/Unhide entries and lanes | M | done | `crates/workspace/src/store` tests, `crates/sessions/tests/session/matrix.rs`, `src/session/project.rs`, `{shell/WorkspaceTree,viewer/{PlaylistViewer,hidden},playback/{LaneGrid,VideoViewer}}.test.*` | Kept in the Doc, never saved ([CONTEXT](../../CONTEXT.md)). Show hidden lists them dimmed; next/previous skip them; hidden lanes undrawn |
| WS-09 | Bookmarks and reviewed marks; Bookmarks view; open at frame | M | done | `crates/workspace/src/store/bookmarks.rs`, `tests/it/bookmarks.rs`, `bookmarks/` |  |
| WS-10 | Bookmarks to CSV | S | done | `tests/it/bookmarks.rs`, `make test-desktop` |  |
| WS-11 | Sidebar views Workspace, Live, Offline, Bookmarks, Plugins; search, sort, filter | M | done | `shell/{Sidebar*,WorkspaceTree*,OfflineAdd,ActivityBar,treeModel}.test.*`, `live/rows.test.ts` | Search, Sort, folds and width outlive a view switch; viewers reveal their item. Same-named rows (LOB-014) and cameras get a detail line: place, stream state. Offline has Add; found cameras match held hosts, expanded; Plugins flags failures. Offline sorts by Location |
| WS-12 | Start panel and welcome area | M | done | `shell/{Shell,WorkspaceFiles,Sidebar}.test.tsx` | Only while empty; heading only with recents; Open Workspace default; Live: AC-03 |
| WS-13 | Split viewer area: drag to split, preview vs pinned panes | M | done | `viewer/*.test.*`, `shell/{SplitArea,headerFailure}.test.*`, `playback/{budget,videoDetails}.test.ts`, `desktop/smoke-video.cjs` header details | Header menu: the row's, without Edit |
| WS-14 | Long playlists load collapsed | S | done | `shell/reveal.test.ts` |  |
| WS-15 | Hover cards with thumbnails | S | in progress | `tests/it/{local_video,glance}.rs`, `shell/{WorkspaceTree*,LiveView}.test.tsx` | Offline, Live |

## Adding content

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| AC-01 | Add Video with any number of overlays; decoder picked by extension | M | done | `crates/workspace/src/intake.rs`, `src/{cli/tests,routes/intake}.rs`, `tests/it/{playback_ws,local_video}.rs`, `desktop/args.test.cjs`, `shell/{Shell.addVideo,AddVideoDialog}.test.tsx`, `crates/handlers` tests | A remote window asks for a path; an unreadable file keeps Add off |
| AC-02 | Drop video files on the window | M | dropped |  | See [deviations](workspace-deviations.md) |
| AC-03 | Add Live Stream with analytics discovery | M | done | `shell/AddLiveStreamDialog.test.tsx`, `src/session/reader/tests.rs` | Edit keeps the saved login on the same camera |
| AC-04 | Add Playlist through a resolver form | M | done | `shell/{AddPlaylistDialog,WorkspaceTree}.test.tsx`, `resolvers/*.test.*`, `crates/handler-api/tests/form.rs`, `src/routes/{handlers,resolve,scan}.rs`, `src/cli/tests.rs`, `tests/it/{local_video,mot_playlist}.rs`, `make test-desktop` | Resolves before adding, so a failure stays in the dialog; a resolver may return several. The CLI playlist waits for a local window |
| AC-05 | One intake validation for dialogs and the CLI | M | done | `crates/workspace/src/intake.rs`, `src/cli/tests.rs`, `tests/it/{local_video,mot_playlist}.rs` | Dialogs and CLI use `workspace::intake` |

## Live

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| LV-01 | RTSP video with one overlay: embedded ONVIF metadata, MQTT or DataHub | M | done | `camera/tests/it/{rtsp,mqtt,datahub}.rs`, `crates/live` | |
| LV-02 | Per-feed status: connected, reconnecting, error, stalled; Retry | M | done | `crates/live`, `live/{words,session}.test.ts` | |
| LV-03 | Pause keeps the connection; stop is terminal | M | done | `crates/live`, `live/session.test.ts` | |
| LV-04 | Capture-time synchronization of frames and overlays | M | done | `overlay-sync`, `crates/live` | |
| LV-05 | Network scan with optional sign-in; add found camera | M | done | `camera/tests/it/scan.rs`, `shell/LiveView.test.tsx` | |
| LV-06 | Streams start only on explicit action | M | done | `crates/workspace/src/store` tests, `sessions` streams, `shell/LiveView.test.tsx` | |
| LV-07 | Live Events list keeps recording while hidden; no seekable history | M | done | `live/session.test.ts` | |
| LV-08 | Temporary device configuration (MQTT analytics publisher) removed on stop and quit | M | done | `camera/tests/it/mqtt.rs`, `crates/live` | |
| LV-09 | RTSP through the camera's web API (HTTP tunnel); a custom stream URL as typed | S | done | `camera/tests/it/tunnel.rs`, `workspace` live tests, `shell/liveForm.test.ts` | Untested on a camera |
