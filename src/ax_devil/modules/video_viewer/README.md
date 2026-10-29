# video_viewer module

Purpose: application-specific Video Viewer workflows for live and offline inspection.

## Quick map

- `live_video_viewer.py`: live Workspace viewer widget and live workflow composition.
- `offline_video_viewer.py`: offline and playlist Workspace viewer widget; `offline_transport.py` holds its shared
  transport bar and playback speed, and `offline_viewer_widgets.py` its overlay host, entry navigation bar and lane menu.
- `stream_media_controller.py`: live source lifecycle, connection status, retry, and `QtStreamSync` orchestration;
  `live_transports.py` opens its RTSP and overlay transports.
- `live_connection.py`: `LiveConnectionStatus` model; per-feed (video, overlay) state, reason, and display text.
- `offline_entry_media.py`: `open_entry_media` opens an entry's sources on a worker thread and delivers `EntryMedia`.
- `entry_preparation.py`: warms the next playlist entry's caches in a child process. It is cancelled by killing its
  process tree, not by a `CancelToken`; caches are written atomically, and the owner deletes the temporary files the
  killed child left.
- `offline_viewer_runtime.py`: `OfflineSession`; composes `offline_playback.py` (source pooling and playback), viewport sync,
  cleanup, and player composition. `offline_lane.py` holds `OfflineLane` (direct overlay lookup, presenter use and
  background analyses), `offline_lane_builder.py` builds the lane displays, `offline_frame_pool.py` the pooled
  sources and their frame relays, and `offline_export.py` the export of lanes.
- `offline_viewer_navigation.py`: playlist entry and consideration navigation policy.
- `scene_frame_presenter.py`: frame plus Scene overlay presentation assembly for live and offline display.
- `scene_inspection.py`: Scene inspection update payloads and the side-panel delivery contract.
- `overlay_persistence.py`: sample-age selection for indexed offline overlays and cached live overlays.
- `lane_grid.py`: shared lane-grid dimensions for offline display and export.
- `export/export_job.py`: synchronous decode, presentation, tiling, encoding, cancellation, and atomic output replacement;
  borrows frozen lane inputs and owns rendering/output resources on the GUI thread.
- `export/export_dialog.py`: lane and quality options, destination prompt, progress, and cancellation intents.
- `export/encoder.py`: the video encoder and its compression presets.
- `timing_diagnostics_widget.py`: each offline lane's timing and overlay-alignment status, mounted in the media
  tools panel's Overlay sync section.
- `loading_indicator.py`: loading overlay used by offline entry changes.
- `media_tools/`: viewer-owned side-panel pages and controls: the Objects and Events lists with their object and event
  pages, the Objects filter drawer, the Appearance popover, and overlay persistence controls. `MediaToolsPanel` is the
  Scene inspector sink of its viewer.

## Boundaries

- `video_viewer` owns workflows inside an open Video Viewer.
- Offline viewer ownership is split between `OfflineVideoViewerWidget` as the workflow shell and `OfflineSession` as the
  active entry runtime. `OfflinePlayback` owns primary frame position and playback state, and each `OfflineLane` owns one
  visible display lane plus media tools, overlay selection, presentation, background analysis, and frozen export inputs.
- Offline lanes delegate frame plus Scene assembly to `SceneFramePresenter`; they do not create or own Qt sync adapters.
- `SceneFramePresenter` returns display and inspection data. Live controllers and offline lanes display frames first, then
  deliver Scene inspection updates. The panel owns which visible inspection work is due.
- `workspace` owns sessions, content descriptions, browser/layout, and viewer hosting.
- `video_player` owns reusable frame display, controls, viewport state, and generic overlay drawing.
- `scene` owns Scene model behavior, inspection helpers, filtering helpers, and Scene-to-drawing preparation.

Do not move Workspace session/layout behavior, reusable player primitives, or pure Scene behavior into this module.

## Rules

- Tools that follow playback do no work while they cannot be seen: a collapsed side panel hides its content, and
  hidden lists and pages keep only the latest update and catch up when shown. The live Events list still records
  while hidden, since those events cannot be recovered, and records each overlay's events once.
- Media tools and zoom shortcuts act on the lane holding keyboard focus, including in lane fullscreen, or the first
  lane when focus is elsewhere.
- Multi-lane export tiles the selected lanes in the viewer's lane grid and follows the first selected lane's frame
  index and timing, as playback does; a lane past its last frame holds that frame.
- A lane's whole-file analyses (scene history, overlay alignment) run on a worker over a snapshot, poll cancellation
  and stop early once superseded. When one fails, the last results stay shown, marked out of date with the lane and
  reason, until a later change succeeds: a sync change for either, a sticky change for the history only.
