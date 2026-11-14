# ax-devil

Shared language for ax-devil's inspection workspace. Code, docs and UI use these names; rules live in
[the invariants](docs/domain/invariants.md).

## Language

| Term | Meaning | Avoid |
|------|---------|-------|
| Video Content | An openable description of image frames over time. | Video source, player, media object |
| Seekable Video Content | Video Content whose frames can be revisited by position. | Seekable source, local source |
| Live Video Content | Video Content whose frames are produced continuously as current frames arrive. | Live source, stream source |
| Overlay Content | Companion data for a video lane, usually a time-varying Scene stream or file. | Overlay source, scene, track file, attachment |
| Overlay Source Kind | The typed lane source identity: file, RTSP, MQTT, DataHub WebSocket, or none. | UI label switch, raw string |
| Playlist Content | An ordered inspection sequence made from entries and lanes. | Playlist source, playlist viewer |
| Lane Slot | A lane's identity within an entry, set by whoever made the overlay: a video's lane label, a Folder Pair's `overlay`, a MOT detector's name. The workspace keeps a lane's matching under it, so every entry of every playlist of an item shares it; hiding a lane is per entry and by label, matching is per item and by slot. | Lane position, lane index |
| Entry Lane | One visible comparison panel inside a playlist entry or standalone video expansion. | Overlay row, panel config |
| Hidden | A playlist entry or lane skipped in playback and omitted from sidebar lists by default. Revealing hidden items lists it dimmed without restoring playback. | Left out, excluded, disabled |
| Workspace | An ordered collection of Workspace Items, Untitled until named, and the name is saved in the file. Saved as a `.ax-devil.workspace` file; unsaved edits are kept as a recovery copy and restored on the next launch. Exclusions, open viewers, and browser rows are runtime state, not part of it. | Window, shell, content list, session |
| Workspace Item | A recipe for one thing to work with, with a stable id and a kind; it resolves into Content. Without a label it is shown by its default name, derived from the recipe and never saved. | Startup request, entry, source |
| Video Item | A Workspace Item for a video file with optional overlay files and decoders. | Video file request |
| Live Stream Item | A Workspace Item for a camera stream with optional live overlay settings; credentials are kept as entered. | Live startup |
| Playlist Item | A Workspace Item holding a playlist resolver id and that resolver's settings; it resolves to one or more playlists. | Resolved playlist |
| Bookmark | A frame of a local video or playlist entry that the user marked while reviewing, with a kind and an optional note; saved in the Workspace with each Bookmark Target's reviewed mark. Never on live streams. | Marker, annotation, flag |
| Bookmark Target | What is reviewed: a Video Item, or one entry of a Playlist Item, named by its playlist's name and its label. | Entry index, content id |
| Session | A piece of shared work held by one backend from its first connect until it ends; every window is attached to exactly one. Private until an Owner uses Start sharing (Stop sharing makes it private again); Sessions of one backend are isolated from each other. | Room, window |
| Owner | The person who controls the server: a local connection or a Server Password holder, on any number of devices. Every Owner seat in a Session drives its Presenter View. | Host, presenter |
| Guest | Anyone in a Session by Join Link: Following shows the Presenter View, Exploring shows its own view of what the Owner has open. | Watcher, Viewer, spectator |
| Presenter View | The one view state of a Session, which Owner seats drive and Guests follow. | Shared screen |
| Join Link | A URL whose secret admits its holder to one Session only. | Invite, share link |
| Server Password | The secret that lets someone use a remote server itself: own and attach to Sessions, add its videos, change its settings. | Login, token |
| Sidebar View | What the sidebar shows, chosen from the activity bar: the Workspace or one part of it (top zone), or something about the application (bottom zone). | Panel, tab, page |
| Content | What a Workspace Item resolves to: Seekable Video, Live Video, or Playlist Content. Rebuilt on every resolution, never saved. | Startup content, resolved item |
| Viewer | An open inspection surface hosted by the Workspace: the user-facing name for a Viewer Widget. | Player, pane, runtime bucket |
| Viewer Widget | The contract every Viewer implements toward the Workspace host: pane frame, header, pin and close, and lifecycle. | Workspace widget, pane |
| Video Viewer | The application workflow that composes media sources, sync, Scene tools, and frame display. | Player, pane |
| Frame Display | A reusable shell that presents frames and overlays and exposes generic mount points. | Video player, playback widget, viewer |
| Frame Viewport | The lower-level viewing area inside a Frame Display. | Video display widget, renderer |
| Media Time | Integer microseconds on the clock frames and Scenes share. | monotime, wall time |
| Timestamp Matching | Synchronization policy for choosing the overlay timestamp that corresponds to a video frame timestamp. | Provider lookup, sync result |
| Overlay Lookup | File overlay provider behavior that loads a Scene for a frame identity and records whether timestamp, sequence, tolerance, or miss was used. | Timestamp matching, offline sync |
| Scene | The world-model exchange format produced by decoders and consumed by filtering, inspection, and rendering. | Raw overlay payload |
| Score | A decoder's own number for a classification, in any range, shown as a plain number such as `0.87`. Distinct from Confidence; either may be absent. | Confidence, probability |
| Confidence | What a source reports as its confidence in a classification, any finite number, shown plain such as `0.87`. Distinct from Score; either may be absent. | Score, likelihood |
| Scene Render Catalog | A catalog document (schema v4) of rules and ordered layers that the frontend compiles and evaluates over a Sample Item into Marks; a lane's choice of one is part of its Lane Style. | Template file, renderer config |
| Built-in Catalog | The packaged, app-owned, read-only Scene Render Catalog. | Default catalog, packaged default |
| Default Catalog | The Scene Render Catalog new views start from; the Built-in Catalog until the user picks another. | Active catalog, remembered selection |
| Sample Item | The immutable encoding of one Scene sample for presentation: the latest observations as typed columns, keyed by overlay source, data revision and sample, and shared by every lane that views that source. | Overlay message, scene packet |
| Frame Map | Per lane, the sample (or none, or empty) that belongs to each frame of a window, chosen by the backend under the Lane Policy. | Overlay lookup result |
| Lane Policy | What the backend needs to select samples for a lane: match basis, tolerance, sticky and whether the lane is shown. Revisioned and sent by the client. | Lane style, lane view |
| Lane Style | What only the client needs to present a lane: catalog, theme, hidden features, class toggles, filter expression and sticky opacity. The backend never interprets it; an Export Job carries it frozen. | Lane policy, lane view |
| User Catalog | A Scene Render Catalog in a `*.json` file of the user's catalog folder, identified as `user:<file name>` and reloaded as the file changes; the app writes a user catalog only to copy or delete one. | Custom catalog, template |
| Viewport | The zoom (1 to 10) and pan of the picture, one per Video Viewer and shared by its lanes. It travels in the view as the rectangle of the video it shows, so a window of another shape shows at least that region. | Lane zoom, camera |
| Selection | The object the user clicked, as an entity id; client view state that survives seeks and shows in every lane of its source. Not the sample a Frame Map selects for a frame. | Focus, pick |
| Inspector | The object page of the media tools: the Selection's summary from the Sample Item, the other fields from one inspection request. | Side panel |
| File Scope | The Objects list mode that lists every object a lane's whole overlay file holds, kept by the lane's class toggles (on every class the object was recorded with) and the search, asked of the backend once the file is read and never of a Following viewer; Frame Scope lists the frame on screen's objects from its Sample Item. | Whole-file mode, history |
| Export Job | One export of a viewer's lanes to an MP4 file or a PNG of one frame: the Owner asks on the viewer's playback socket, the backend freezes the lanes' catalogs, Lane Styles, Lane Policies and data revisions, and runs it, one at a time, until it ends or the socket closes. | Recording, render job |
| Render Page | The hidden offscreen Electron page that draws an Export Job's pictures from the frozen inputs, with the same presentation code as the screen. | Export window, headless browser |
| Plugin | An installed program, listed in the plugin registry and run as the user, that provides Handlers ([plugins](docs/plugins.md)). | Converter, script, adapter |
| Program | The executable and arguments a Plugin's manifest names (`program`); every decoder, resolver and task call of the Plugin is one run of it. A Plugin of only views and commands has none. | Command, binary |
| Handler | One decoder, playlist resolver, task, view or command, built in or provided by a Plugin, selected by its handler id. A decoder, resolver or task id is unique among all handlers; a view's or command's is local to its Plugin and written `<plugin>.<id>` outside it. | Converter, driver, contribution, operation |
| Lane Reader | A Handler that reads lanes of an entry: a decoder with input `lanes`, or a Task. Listed to the page as `readers`. | Operation |
| Lane Input | One lane a Lane Reader reads: a declared input key mapped to the slot of the lane given for it, checked once by the plugins crate. | Lane argument |
| Lane File | A lane's track as JSON Lines, named by its content hash: what a Lane Reader's request and a view's `scenes.read` carry. | Scene dump, export |
| Derived Lane | A lane a decoder computes from other lanes of the same entry and optionally its video, declared on a Workspace Item by slot and stored like any lane; its identity is its Bundle's digest. | Result, computed track |
| Bundle | The file the host writes for a Derived Lane or Task: its input Lane Files by key with their content hashes, and its video. Only a Derived Lane's Bundle digest is an identity; a Task's Bundle is not. | Job, manifest |
| Task | A Lane Reader that produces no Scenes; it writes where its own options say. One streaming request: progress is the response, cancel is dropping it. | Job, export |
| Admission | Whether a demand may run plugin code: Compute for an Owner seat, Reuse for a Guest seat, decided where the playback lease is redeemed. | Permission, role check |
| Not Built | The error a Guest's open gets for a lane of plugin code with no stored result: Reuse never computes. | Pending, missing |
| Plugin Command | A menu entry and rebindable key of a Plugin that runs one of its own Lane Readers or views, on the app, an item or a lane. | Action, operation |
| Place | Where a Plugin View is shown: the sidebar, or a video layer over one lane's viewer whose on/off state belongs to its window. | Slot, zone |
| Plugin View | A page a Plugin ships, shown in a sandboxed frame in a Place and served under a view address (`/views/<random>/...`); it reaches the app only through the Bus. | Plugin UI, extension, view token |
| Bus | The one channel of messages between a Plugin View and the Owner's page. A Binding is the viewer, item and entry it is bound to; the epoch counts Bindings, and a call made under another epoch is answered `stale`. | Plugin API, RPC |
| Ingest Watermark | While a file is still being read, the sample key below which every frame is decided. Frame Map cells exist only below it, and the first out-of-order key stops it until the read ends. | Progress, loaded up to |
| Marks | The boxes, lines, circles, points, paths and labels a catalog produces from a Sample Item in a lane, laid out and painted by the frontend. | Drawing Instruction, Prepared Drawing |

## Relationships

- Video Content is either Seekable Video Content or Live Video Content.
- Video Content, Overlay Content, and Playlist Content are descriptions. Runtime sources are created only when a viewer opens them.
- How these concepts fit together is in [the architecture overview](docs/architecture/overview.md).

## Usage Notes

- Use **Guest** for a Session's participant who joined by Join Link, never Viewer, which already names the inspection surface.
- Use **Seekable Video Content** for the Workspace description and "seekable source" only for the runtime source opened from it.
- Use **Frame Display** for the reusable display shell and **Video Viewer** for the workflow that owns media behavior.
- Use **Overlay Content** for companion data, **Sample Item** and **Frame Map** for what the backend sends, and **Marks** for what the frontend draws. Use **Lane Policy** for what the backend must know and **Lane Style** for what only the client holds.
- Use **Scene Render Catalog** for the rules and layers that decide how a Sample Item becomes Marks. Classification strings are decoder evidence; which rule matches is catalog policy.
