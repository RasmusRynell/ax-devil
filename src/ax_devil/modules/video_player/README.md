# video_player module

Purpose: reusable frame display UI stack for frame rendering, overlays, mount points, controls, and interaction
orchestration.

## Quick map

- `engine/`
	- `renderer.py`: frame handoff, Quick preparation, viewport interaction, and viewer diagnostics.
	- `quick/`: native video textures, retained shapes, and glyph rendering for display and native-size image export.
	- `viewport_state.py`: zoom/pan state machine and clamping math (no widget dependencies).
	- `render_context.py`: render-time context and pixel-to-normalized helpers.
	- `drawing.py`: generic drawing target, styles and shared text preparation.
	- `quick/preparation.py`: transactional conversion directly into final backend data.
	- `data_types.py`: display payload data types (`VideoFrame`, `VideoOverlayData`, `VideoFrameWithOverlays`).
	- `video_transforms.py`: aspect-preserving frame/display sizing math.
- `ui/`
	- `frame_display.py`: public reusable display shell with viewport forwarding and generic mount points.
	- `viewport.py`: lower-level viewing area around the renderer (background/info HUD, interaction, widget overlays).
	- `hud_painter.py`: HUD drawing helpers used by `viewport.py`.
	- `entity_hover_card.py`: the hover card widget shown over the video for a hit entity.
	- `overlay_layout.py`: pure overlay placement policy for widget overlays.
	- `controls.py`: seekable control panel widgets, including the caller-labelled side panel toggle button.
	- `fading.py`: generic fading widget behavior and hover notification bridge.
	- `draggable.py`: draggable handle/panel widgets.
- `orchestration/`
	- `control_visibility.py`: hover + idle visibility policy controller.
	- `side_panel_controller.py`: side panel + drag handle orchestration; `FrameDisplay` exposes its open state
	  (`is_side_panel_open()`, `set_side_panel_open()`, `sidePanelToggled`).
	- `interaction_types.py`: lightweight interaction protocols (for typed wiring).
	- `fullscreen.py`: `LaneFullscreenController` (see Lane fullscreen below).
- `constants.py`: shared timing/layout/style constants.

## Runtime flow

1. Controller/domain layer sends `VideoFrameWithOverlays` to `FrameDisplay.display_frame(...)`.
2. `FrameDisplay` forwards to `FrameViewport.display_frame(...)`.
3. `VideoFrameRenderer` coalesces delivery and prepares a complete frame for the Quick surface on the GUI thread.
   Graphics Off uses Qt Quick software rendering; file export uses the same surface through `FrameImageRenderer`.
4. `RenderContext` is built (cached by target size) and passed with the surface-owned `DrawingBuffer` (carrying exact `DrawingSettings`) to drawing generators.
5. Widget overlays (controls, drag handle) are laid out by `overlay_layout.py` policy.

## Quick surface

`VideoFrameRenderer` is a QWidget shell around one `QQuickWidget` that draws the video texture and every overlay;
Graphics Off selects Quick's software backend at startup. QQuickWidget keeps widget stacking at the cost of an extra
composition pass and no threaded render loop, so all preparation stays on the GUI thread and the scene graph only
consumes prepared data. Rules worth knowing before changing `engine/quick/`:

- Geometry, paths, text and labels are separate pools and layers drawn bottom to top in that order. Order across
  entities is not preserved so geometry can batch into a few nodes (split at 60,000 vertices).
- Geometry items are matched by ordinal; path and text items by content, independent of position, so moving an
  overlay updates transforms instead of rebuilding glyphs and tessellation.
- Labels are painted into a sprite once per distinct content, scale, DPI and DPR, and submitted through one
  `LabelLayer` whose node tree shares textures by sprite, so returning content such as a repeated score is not
  rasterized or uploaded again while its sprite and texture are still cached. Cache limits are in the code; visible
  groups are never evicted, and scene-graph recreation releases everything.
- Culling uses the surface viewport in overlay-local coordinates, not the image bounds, so overlays stay visible in
  letterboxing; pan and viewport-size changes therefore invalidate preparation.
- Cleanup submits an empty frame and synchronizes the scene graph before hiding, so Qt deletes native image nodes
  while their Python texture adapters are still alive. The export renderer is destroyed explicitly after cleanup, on failure too.

## Design rules

- This module is application-independent. It must not import Workspace, Video Viewer workflow, data-source runtime
  orchestration, Scene adapters, or overlay-persistence tools.
- Keep queued frame preparation in `engine/renderer.py` minimal and deterministic.
- Keep viewport math in `engine/viewport_state.py`; do not re-embed zoom/pan rules in widgets.
- Keep interaction policies (hover/idle visibility, side panel orchestration) in dedicated controllers.
- Keep layout policy pure in `ui/overlay_layout.py`; apply side effects in callers.
- `FrameDisplay` must not own media-source, synchronization, live/offline mode, playlist, or Scene-tool decisions.
- Keep controls/widgets thin; workflow orchestration belongs in Video Viewer controllers.

## Extension points

- New drawing instruction: extend `engine/drawing.py` and `quick/preparation.py`, then emit it directly from scene rendering.
- New widget overlay placement mode: add enum/policy in `ui/overlay_layout.py`.
- New display mount behavior: add it to `FrameDisplay` only when it is generic and workflow-independent.
- New seekable display behavior policy: compose `FrameDisplay` + control panels in
  `ax_devil.modules.video_viewer`, where workflow orchestration belongs.
- New HUD visuals: update `ui/hud_painter.py` without touching render orchestration.

## Lifecycle and cleanup

- `FrameDisplay.cleanup()` is the entry point for reusable display teardown.
- `FadingWidget.cleanup()` must be called for fading widgets during teardown.
- Side panel resources are cleaned via `SidePanelController.cleanup()`.

## Testing focus

- Viewport behavior: `tests/modules/video_player/test_video_renderer_zoom.py`.
- Metrics identity behavior: `tests/modules/video_player/test_video_renderer_metrics.py`.
- Transform sizing math: `tests/modules/video_player/test_video_transforms.py`.
- Keep renderer tests focused on behavior contracts, not internal private fields.

Rendering diagnostics use `modules/diagnostics/render_metrics.py`; viewer owners set a content/lane label through
`display.viewport.set_diagnostics_label(...)`. Timing boundaries are in the
[measurement definitions](../diagnostics/README.md#measurement-definitions).

## Lane fullscreen

Lane fullscreen (keys in [Usage](../../../../docs/usage.md#video-and-overlays)) opens on the monitor containing the
lane's center. `LaneFullscreenController` temporarily hosts the existing `FrameDisplay` in a
`ChromeWindow`; sources, synchronization, and sibling lanes remain owned by the viewer
and keep running. Playback and playlist shortcuts reuse the main window actions.
Multi-lane transport controls stay in the workspace; use playback shortcuts while fullscreen.
Display cleanup restores the lane before entry replacement or viewer teardown.

Viewport resizing preserves normalized zoom and pan, using the same coordinates as
lane synchronization. Temporary pan clamping in a different aspect ratio does not
overwrite the saved view; explicit zoom/pan interaction establishes a new saved view.
