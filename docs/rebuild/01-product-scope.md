# 01 Product Scope

This file is the only authority on delivery priority (**M** = needed for parity, **S** = may follow with owner
consent). Constraints elsewhere apply whenever a feature exists; they do not set release scope.

## Purpose and users

A desktop tool for inspecting video with analytics overlays, live from Axis cameras and from recordings, object by
object and frame by frame, and for comparing results side by side. Take the pitch and built-in formats from
`README.md`, released scope and known limits from `CHANGELOG.md`.

| User | Needs |
|------|-------|
| Analytics / CV developer | Compare model runs with ground truth, find ID switches, check timing alignment |
| Camera integrator / tester | Connect to cameras, see live analytics over RTSP/MQTT/DataHub, find cameras |
| Dataset reviewer | Step through playlists, bookmark frames, mark entries reviewed |
| Coding agent for any of the above | Write decoders, resolvers, catalogs; verify headlessly |

- **MUST** stay AI-first: decoders, resolvers and render catalogs are text an agent edits, each with a headless
  verification command. No in-app catalog editor (`docs/architecture/catalog-viewer.md#design-rules`).
- **MUST** treat live viewing and offline review as equals sharing display, rendering, inspection and catalogs.
- Platform: Linux supported; CI runs the suite on Windows and macOS; plugin management is Linux-only (D2). No audio.

## Parity rows

"Source" defines current behavior; read it before building the row.

### Workspace

| ID | Feature | P | Source |
|----|---------|---|--------|
| WS-01 | Ordered items (video, live stream, playlist); Untitled until named | M | `docs/architecture/workspace.md` |
| WS-02 | New, Open, Save, Save As, recent workspaces | M | `docs/architecture/workspace.md#lifecycle` |
| WS-03 | No prompts; recovery copy, restore banner, per-instance lock | M | same |
| WS-04 | Unreadable items preserved | M | `docs/architecture/workspace.md#file-format` |
| WS-05 | Background resolution; pending and unavailable rows | M | `docs/architecture/workspace.md#resolution` |
| WS-06 | Re-resolve on file change; reload offered to open viewers | M | same, `src/ax_devil/modules/file_watching/README.md` |
| WS-07 | Rename, Edit, Remove, item information | M | `docs/domain/invariants.md#content-model` |
| WS-08 | Hide/Unhide entries and lanes | M | `CONTEXT.md` "Hidden" |
| WS-09 | Bookmarks and reviewed marks; Bookmarks view; open at frame | M | `docs/architecture/workspace.md#bookmarks` |
| WS-10 | Bookmarks to CSV | S | `workspace/core/bookmark_csv.py` |
| WS-11 | Sidebar views Workspace, Live, Offline, Bookmarks, Plugins; search, sort, filter | M | `docs/architecture/ui-framework.md#sidebar-views` |
| WS-12 | Start panel and welcome area | M | `docs/architecture/ui-framework.md#workspace-shell` |
| WS-13 | Split viewer area: drag to split, preview vs pinned panes | M | `docs/architecture/ui-framework.md#center-area` |
| WS-14 | Long playlists load collapsed | S | `docs/usage.md#workspaces` |
| WS-15 | Hover cards with thumbnails | S | `thumbnails/`, `docs/usage.md#sidebar-views` |

### Adding content

| ID | Feature | P | Source |
|----|---------|---|--------|
| AC-01 | Add Video with any number of overlays; decoder picked by extension | M | `docs/architecture/overview.md#workspace-items-and-intake` |
| AC-02 | Drop video files on the window | M | `docs/architecture/ui-framework.md#workspace-shell` |
| AC-03 | Add Live Stream with analytics discovery | M | `workspace/ui/add_content/`, `docs/usage.md#live-cameras` |
| AC-04 | Add Playlist through a resolver form | M | `docs/architecture/workspace.md#playlist-resolver-contract` |
| AC-05 | One intake validation for dialogs, CLI and drops | M | `docs/domain/invariants.md#content-model` |

### Live

| ID | Feature | P | Source |
|----|---------|---|--------|
| LV-01 | RTSP video with one overlay: embedded ONVIF metadata, MQTT or DataHub | M | `docs/architecture/overview.md#live-source-composition` |
| LV-02 | Per-feed status: connected, reconnecting, error, stalled; Retry | M | same |
| LV-03 | Pause keeps the connection; stop is terminal | M | `docs/domain/invariants.md#data-pipeline` |
| LV-04 | Capture-time synchronization of frames and overlays | M | `docs/domain/invariants.md#frame-identity` |
| LV-05 | Network scan with optional sign-in; add found camera | M | `docs/usage.md#sidebar-views` |
| LV-06 | Streams start only on explicit action | M | same |
| LV-07 | Live Events list keeps recording while hidden; no seekable history | M | `src/ax_devil/modules/video_viewer/README.md#rules` |
| LV-08 | Temporary device configuration (MQTT analytics publisher) removed on stop and quit | M | `data_sources/live/mqtt_overlay_source.py`, `DataSource.wait_for_retired` in `data_sources/base.py` |

### Offline playback and comparison

| ID | Feature | P | Source |
|----|---------|---|--------|
| OF-01 | Play/pause, step, speed, seek; clock-following playback | M | `docs/domain/invariants.md#display-rendering` |
| OF-02 | Comparison lanes: several overlays on one video, or lanes with different videos (playlist entries) | M | `CONTEXT.md` "Entry Lane", `video_viewer/offline_playback.py`, `src/ax_devil/modules/video_viewer/README.md#rules` |
| OF-03 | Playlists of entries and lanes; next/previous entry | M | `docs/architecture/overview.md#synchronization` |
| OF-04 | Timestamp matching, sequence-keyed files, sticky overlays | M | `docs/domain/invariants.md#frame-identity` |
| OF-05 | Timing and alignment diagnostics per lane | M | `src/ax_devil/modules/video_viewer/README.md` |
| OF-06 | Decoded-frame cache under one memory allowance | M | `docs/settings.md#video-cache-memory` |
| OF-07 | Prepare next playlist entry in the background | S | same |
| OF-08 | Export with overlays: lane selection, quality presets, cancellable | M | `docs/domain/invariants.md#export` |

OF-02 policy: secondary lanes follow the primary lane's frame index, clamped to their own last frame; export follows
the first selected lane and holds shorter lanes on their last frame. Parity case: two lanes with different lengths and
frame rates play, seek, step and export without errors and with that alignment.

### Inspection and display

| ID | Feature | P | Source |
|----|---------|---|--------|
| IN-01 | Media tools panel per lane with Objects and Events lists, object and event pages | M | `video_viewer/media_tools/` |
| IN-02 | Object filters by type and score/confidence range; whole-file filtering with declared policies | M | `.agents/skills/write-plugin/reference.md#decoder-classification-filters`, `filtering/` |
| IN-03 | Hover chip and click selection; can be turned off | M | `docs/settings.md#video-overlays` |
| IN-04 | Frame info HUD, lane names | M | same |
| IN-05 | Zoom, pan, reset, synced across lanes; lane and app fullscreen | M | `src/ax_devil/modules/video_player/README.md#lane-fullscreen` |
| IN-06 | Per-view overlay detail visibility | M | `docs/usage.md#overlay-details` |
| IN-07 | Per-view sticky/sync/filter settings, Apply to all, defaults for new views | S | `video_viewer/view_settings.py` |
| IN-08 | Structured query search in Objects and Events lists | M | `video_viewer/media_tools/query_bar.py`, `video_viewer/media_tools/objects_page.py`, `video_viewer/media_tools/events_page.py` |
| IN-09 | Frame vs File scope for objects | M | `video_viewer/media_tools/object_scope.py` |
| IN-10 | Pinned fields kept across object pages | S | `object_inspection/pinned_fields.py` |
| IN-11 | Copy a field or section value | S | `object_inspection/fields_view.py` |
| IN-12 | Presence strip with seeking; previous/next appearance | M | `video_viewer/media_tools/presence.py`, `video_viewer/media_tools/object_page.py` |
| IN-13 | Event to frame (offline; live events have no seekable history); event/object cross-navigation | M | `video_viewer/media_tools/event_page.py`, `video_viewer/media_tools/object_page.py` |

### Render catalogs

| ID | Feature | P | Source |
|----|---------|---|--------|
| RC-01 | Catalog language (schema v3) and the six built-ins | M | `.agents/skills/render-catalog/SKILL.md` |
| RC-02 | Per-view selection, Apply to all, Use as default, hot reload | M | `src/ax_devil/modules/scene/rendering/README.md#catalog-files-and-selection` |
| RC-03 | Catalog viewer window with seeded sheets, New copy, Delete | M | `docs/architecture/catalog-viewer.md` |
| RC-04 | `catalog list/check/render/new/use/reference` | M | same |

### Plugins

| ID | Feature | P | Source |
|----|---------|---|--------|
| PL-01 | Decoder plugins (file, payload) and playlist resolver plugins | M | `.agents/skills/write-plugin/SKILL.md` |
| PL-02 | Built-in decoders and resolvers listed by `list-handlers` and `playlist --help` | M | `src/ax_devil/plugins/` |
| PL-03 | Install, update, remove, list from CLI and Plugins view | M | `docs/plugins.md` |
| PL-04 | Load failures never stop the app and are listed | M | `docs/domain/invariants.md#plugin-system` |
| PL-05 | Unchanged third-party Python plugins run (bridge); not planned by default | S | D1 |

### Settings and application

| ID | Feature | P | Source |
|----|---------|---|--------|
| ST-01 | Settings dialog pages; restart marker | M | `docs/settings.md` |
| ST-02 | Quick Setup on first start | M | `docs/settings.md#quick-setup` |
| ST-03 | Theme and text size, applied live | M | `docs/settings.md#text-size` |
| ST-04 | `$VARIABLE` references | M | `docs/settings.md` |
| ST-05 | Keyboard shortcuts, dialog, override persistence | M | `shortcuts/shortcuts.py` (`DEFAULT_SHORTCUTS`) |
| ST-06 | Workspace credential references expand only for their bound host or a confirmed one; warn before saving a literal password (new) | M | D18, [13](13-security-packaging-ops.md#threat-model) |
| AP-01 | Uncaught errors logged; continue or close | M | `application_shell/exception_handler.py` |
| AP-02 | Restart from inside the app | M | `application_shell/restart.py` |
| AP-03 | `clear-cache` | S | `ax-devil clear-cache --help` |
| AP-04 | About dialog | S | `application_shell/about_dialog.py` |
| AP-05 | Custom window frame option | S | D7 |
| AP-06 | Open logs folder / copy diagnostics (new) | S | [13](13-security-packaging-ops.md#observability) |

### Developer tooling

| ID | Tool | P | Source |
|----|------|---|--------|
| DT-01 | Isolated GUI test environment for agents and CI | M | [09](09-testing.md#isolation-must) |
| DT-02 | UI screenshot harness | M | `tools/ui_screenshots.py` |
| DT-03 | Rendering benchmarks with A/B against a ref | S | tools/bench (`tools/bench/README.md`) |
| DT-04 | Timestamp alignment report | S | `tools/README.md` |
| DT-05 | Release check in clean environments | S | `tools/release-check/` |
| DT-06 | Demo recorder | S | tools/demo_recorder (`tools/demo_recorder/README.md`) |

Out of scope (no repo evidence): in-app catalog editing, audio, recording from cameras, cloud services.
