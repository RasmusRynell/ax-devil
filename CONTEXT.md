# ax-devil

Shared language for ax-devil's inspection workspace. This file names the current concepts in code; rules and invariants live in `docs/domain/invariants.md`.

## Language

| Term | Meaning | Avoid |
|------|---------|-------|
| Video Content | An openable description of image frames over time. | Video source, player, media object |
| Seekable Video Content | Video Content whose frames can be revisited by position. | Seekable source, local source |
| Live Video Content | Video Content whose frames are produced continuously as current frames arrive. | Live source, stream source |
| Overlay Content | Companion data for a video lane, usually a time-varying Scene stream or file. | Overlay source, scene, track file, attachment |
| Overlay Source Kind | The typed lane source identity: file, RTSP, MQTT, DataHub WebSocket, or none. | UI label switch, raw string |
| Playlist Content | An ordered inspection sequence made from entries and lanes. | Playlist source, playlist viewer |
| Entry Lane | One visible comparison panel inside a playlist entry or standalone video expansion. | Overlay row, panel config |
| Workspace | The active session state: its Content and which items are not considered. Open, focused, and pinned viewers live in the hosted widgets; browser rows are derived. | Window, shell, content list |
| Startup Request | CLI or bootstrap input that resolves into Workspace content before it is added. | Startup content, initial content |
| Viewer | An open inspection surface hosted by the Workspace. | Player, pane, runtime bucket |
| Video Viewer | The application workflow that composes media sources, sync, Scene tools, and frame display. | Player, pane |
| Frame Display | A reusable shell that presents frames and overlays and exposes generic mount points. | Video player, playback widget, viewer |
| Frame Viewport | The lower-level viewing area inside a Frame Display. | Video display widget, renderer |
| Timestamp Matching | Synchronization policy for choosing the overlay timestamp that corresponds to a video frame timestamp. | Provider lookup, sync result |
| Overlay Lookup | File overlay provider behavior that loads a Scene for a FrameIdentifier and records whether timestamp, sequence, tolerance, or miss was used. | Timestamp matching, offline sync |
| Scene | The world-model exchange format produced by decoders and consumed by filtering, inspection, and rendering. | Raw overlay payload |
| Scene Render Catalog | A selected rendering setup that owns reusable templates and catalog-defined recipes for Scene entities. | Template file, renderer config |
| Built-in Catalog | The packaged, app-owned, read-only Scene Render Catalog. | Default catalog, packaged default |
| Default Catalog | The Scene Render Catalog new views start from; the Built-in Catalog until the user picks another. | Active catalog, remembered selection |
| Drawing Instruction | A catalog operation (box, text, line, point, polygon or circle) emitted directly to the drawing target. JSON still calls these `primitive` steps. | Intermediate drawable object |
| Prepared Drawing | Final vertex bytes, resolved path properties and shaped text ready for retained Qt item binding. | Primitive list |
| Draw Recipe | Catalog-owned entity rendering logic that emits Drawing Instructions. | Object renderer, type switch |
| Cached Scene Overlay | The adapter that keeps one Scene and provides prepared drawings, hover hits, and preparation metrics to the video player. | Render cache, Scene renderer |

## Relationships

- Video Content is either Seekable Video Content or Live Video Content.
- Video Content, Overlay Content, and Playlist Content are descriptions. Runtime sources are created only when a viewer opens them.
- How these concepts are built and routed is in `docs/architecture/overview.md`; their rules are in
  `docs/domain/invariants.md`.

## Usage Notes

- Use **Seekable Video Content** for the Workspace description and `Seekable*Source` for runtime source capability.
- Use **Frame Display** for the reusable display shell and **Video Viewer** for the workflow that owns media behavior.
- Use **Overlay Content** for companion data and **Drawing Instruction** for catalog operations and **Prepared Drawing** for backend submission data.
- Use **Scene Render Catalog** for task-selected rendering setups that own templates and catalog-defined recipes.
- Use **Draw Recipe** for catalog-owned entity rendering behavior. Classification strings are decoder evidence; recipe selection is Scene Render Catalog policy.
