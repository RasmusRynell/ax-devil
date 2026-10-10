# video_viewer module

Purpose: application-specific Video Viewer workflows for live and offline inspection.

## Quick map

- `live_video_viewer.py`: live Workspace viewer widget and live workflow composition.
- `offline_video_viewer.py`: offline and playlist Workspace viewer widget.
- `stream_media_controller.py`: live source lifecycle, connection status, retry, and `QtStreamSync` orchestration.
- `live_connection.py`: `LiveConnectionStatus` model; per-feed (video, overlay) state, reason, and display text.
- `offline_viewer_runtime.py`: `OfflineSession` and `OfflineLane`; owns offline source pooling,
  direct overlay lookup, timeline commands, viewport sync, cleanup, and player composition.
- `offline_viewer_navigation.py`: playlist entry and consideration navigation policy.
- `scene_frame_presenter.py`: side-effect-free frame plus Scene overlay presentation assembly for live and offline display.
- `scene_inspection.py`: Scene inspection update payloads and queued side-panel delivery helpers.
- `overlay_persistence.py`: sample-age selection for indexed offline overlays and cached live overlays.
- `lane_grid.py`: shared lane-grid dimensions for offline display and export.
- `export/export_job.py`: synchronous decode, presentation, tiling, encoding, cancellation, and atomic output replacement;
  borrows frozen lane inputs and owns rendering/output resources on the GUI thread.
- `export/export_dialog.py`: lane and quality options, destination prompt, progress, and cancellation intents.
- `loading_indicator.py`: loading overlay used by offline entry changes.
- `media_tools/`: viewer-owned side-panel widgets for filtering, Scene inspection, the Scene event log, object
  history, and overlay persistence controls. `MediaToolsPanel` is the Scene inspector sink of its viewer.

## Boundaries

- `video_viewer` owns workflows inside an open Video Viewer.
- Offline viewer ownership is split between `OfflineVideoViewerWidget` as the workflow shell and `OfflineSession` as the
  active entry runtime. `OfflineSession` owns primary frame position and playback state, and each `OfflineLane` owns one
  visible display lane plus media tools, direct overlay lookup, presenter use, and async non-primary frame rendering.
- Offline lanes delegate frame plus Scene assembly to `SceneFramePresenter`; they do not create or own Qt sync adapters.
- `SceneFramePresenter` returns display and inspection data. Live controllers and offline lanes display frames first, then
  schedule Scene inspection updates for side panels.
- `workspace` owns sessions, content descriptions, browser/layout, and viewer hosting.
- `video_player` owns reusable frame display, controls, viewport state, and generic overlay drawing.
- `scene` owns Scene model behavior, inspection helpers, filtering helpers, and Scene-to-drawing preparation.

Do not move Workspace session/layout behavior, reusable player primitives, or pure Scene behavior into this module.

## Rules

- Tools that follow playback do no work while they cannot be seen: a collapsed side panel hides its content, and
  hidden lists, tabs and cards keep only the latest update and catch up when shown. Live event logs still record
  while hidden, since those events cannot be recovered, and record each overlay's events once.
- Media tools and zoom shortcuts act on the lane holding keyboard focus, or the first lane when focus is elsewhere.
- Multi-lane export tiles the selected lanes in the viewer's lane grid and follows the first selected lane's frame
  index and timing, as playback does; a lane past its last frame holds that frame.

## Testing focus

- Live workflow, controller and connection status behavior: `tests/modules/video_viewer/test_live_video_viewer.py`,
  `tests/modules/video_viewer/test_stream_media_controller.py` and `tests/modules/video_viewer/test_live_connection.py`.
- Offline workflow and playlist navigation: `tests/modules/video_viewer/test_offline_video_viewer.py`.
- Offline session playback, direct overlay lookup, source pooling, and lane frame-request behavior:
  `tests/modules/video_viewer/test_offline_runtime.py` and relevant widget-level cases in
  `tests/modules/video_viewer/test_offline_video_viewer.py`.
- Viewer media tools and overlay persistence:
  `tests/modules/video_viewer/media_tools/` and `tests/modules/video_viewer/test_overlay_persistence.py`.
- Export job output and dialog cancellation: `tests/modules/video_viewer/export/`.
- Scene-to-player presenter behavior: `tests/modules/video_viewer/test_scene_frame_presenter.py`.
