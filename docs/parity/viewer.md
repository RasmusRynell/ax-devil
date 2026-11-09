# Parity: offline playback, inspection and render catalogs

Row format: [parity](../parity.md). Deviations: [viewer deviations](viewer-deviations.md).

## Offline playback and comparison

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| OF-01 | Play/pause, step, speed, seek; clock-following playback | M | done | `playback/*.test.*`, `tests/it/playback_ws.rs`, `crates/media/tests/it/*.rs`, `make test-desktop` | Image sequences and variable frame rate share it. An open shows a loading cover; a decode failure stops. Real GPU: D0 spike only |
| OF-02 | Comparison lanes: several overlays on one video; a playlist entry opens at its start, and two recordings side by side are two viewers with no shared clock | M | done | `tests/it/{mot_playlist,playback_lanes}.rs`, `crates/overlays/src/tests/`, `playback/*.test.*`, `desktop/smoke-playlist.cjs` | The frontend draws a lane from Sample Items and its Frame Map (D22–D27); one overlay file can be several lanes (D24) |
| OF-03 | Playlists of entries and lanes; next/previous entry | M | done | `crates/playlist-resolvers/tests/`, `tests/it/mot_playlist.rs`, `viewer/Playlist*.test.tsx`, `crates/sessions/tests/session/revocation.rs`, `make test-desktop` | MOT Challenge and folder-pair playlists, a lane per overlay file. A following window follows the presenter's entry |
| OF-04 | Timestamp matching, sequence-keyed files, sticky overlays | M | done | `crates/overlay-sync/src/select/tests.rs`, `crates/overlays/src/tests/{delivery,statuses}.rs`, `tests/it/mot_playlist.rs`, `playback/{LaneMenuTiming,lanes}.test.*` | Per lane, session only ([invariants](../domain/invariants.md#frame-identity)); Frame Maps carry the choice to the viewer (D26) |
| OF-05 | Timing and alignment diagnostics per lane | M | done | `crates/media/src/index/cadence.rs`, `crates/media/tests/it/{decode,sequence}.rs`, `crates/overlay-sync/src/alignment.rs`, `crates/overlays/src/tests/statuses.rs`, `playback/LaneMenuTiming.test.tsx`, `make test-desktop` | Cadence and lane match; warns when samples end far or any sit on no frame. Image sequences share it |
| OF-06 | Decoded-frame cache under one memory allowance | M | done | `crates/playback/src/tests/`, `tests/it/playback_ws.rs`, `src/video_cache.rs`, `crates/settings` tests | Allowance, equal shares, eviction, live resize, uncached oversized frames work. Cached frames are shaded on the seek bar |
| OF-07 | Prepare next playlist entry in the background | S | done | `tests/it/prepare_next.rs` | `playback.prepare_next_video` |
| OF-08 | Export with overlays: lane selection, quality presets, cancellable | M | done | `crates/{export,media}/`, `tests/export/export.rs`, `src/render_host/`, `{export,render}/`, `make test-desktop` | New: PNG of one frame. [Speed](../../crates/export/README.md#measured): `make bench-export`. Hard links refused on every platform |

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
| IN-13 | Event to frame (offline; live events have no seekable history); event/object cross-navigation | M | done | `scene-store/src/tests/events.rs`, `tools/MediaToolsPanel.events.test.tsx` | Pages by index ([deviations](viewer-deviations.md)) |

## Render catalogs

| ID | Feature | P | Status | Test | Notes |
|----|---------|---|--------|------|-------|
| RC-01 | Catalog language (schema v4) and the six built-ins | M | done | `present/{oracle.test.ts,catalog,layout,text}`, `crates/catalogs` tests | All six match the reference oracle sheets; JSON Schema comes from compiler tables |
| RC-02 | Per-view selection, Apply to all, Use as default, hot reload | M | done | `playback/{LaneMenu,laneCatalog,catalogs}.test.*`, `crates/catalogs` tests, `tests/it/playback_catalogs.rs` | Built-in and `user:<file>`, hot-reloaded or Reload; a failing one keeps its last good. Default: `overlays.default_catalog`; no Other-views line (deviations) |
| RC-03 | Catalog Copy and Delete | M | done | `src/routes/catalogs.rs`, `playback/LaneCatalogActions.test.tsx` | Viewer window and preview sheets dropped by owner decision |
| RC-04 | `catalog` CLI | M | done | `src/cli/catalog_tests.rs`, `tests/export/catalog_check.rs` | `reference` generated; `render` dropped by owner decision |
