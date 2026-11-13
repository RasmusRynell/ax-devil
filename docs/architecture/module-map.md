# Module Map

Where code and files belong. Layers and the dependency rule are in the [overview](overview.md#layers).

| Path | Owns |
|------|------|
| `src/main.rs`, `src/cli.rs`, `src/cli/`, `src/runtime.rs` | Backend startup and shutdown and the command line (the local backend, `local`, `serve`, `app`, and `playlist <resolver>`, `list-handlers`, `catalog` generated from the registry); the items the command line asks for wait in the launch queue; prints the readiness line the shell and supervisor wait for |
| `src/paths.rs` | Where the settings file, caches, logs and the user's catalog folder live: the XDG folders, or the command-line overrides |
| `src/lib.rs`, `src/plugins.rs` | The backend's two entry points (`app`, `lan_app`), the state every route reaches, settings and info handlers, the embedded frontend; the `Plugins` service that loads, installs, removes, updates and live-reloads plugins and is the only code that swaps the handlers |
| `src/routes/` | The route table, the handler, scan and resolve routes (the resolve route holds the resolved item until the session adds it), and the playback WebSocket (redeems a ticket into a stream lease and enforces its authority) with its lane overlays, catalog feed (`playback/catalogs.rs`) inspections (`playback/inspect.rs`) and File scope queries (`playback/file_scope.rs`): adapters over the services that hold only per-viewer state (File scope's per-lane job scheduler and cache, the export request frozen into an `ExportSpec`) |
| `src/routes/{live,network,glance}.rs`, `src/routes/playback/live.rs` | The routes over `crates/live`: the form's camera queries, the network scan (also started at launch by `scan.on_startup`), the sidebar's snapshot and login check, and the Add Live Stream form's placeholders for `$NAME` defaults (`workspace::preview`); the playback socket's live lease; `Services::serve` shuts the live feeds down on quit |
| `src/exports.rs`, `src/render_host.rs`, `src/desktop.rs`, `src/routes/playback/export.rs` | The backend's one export job, the Render Page's host and its private socket, the desktop folder and its Electron, and the playback socket's export messages |
| `src/video_sources.rs`, `src/video_cache.rs` | Where `media` meets `playback` (the decoder adapter, opening viewers on files) and how the `playback.video_cache` setting sizes the cache |
| `src/prepare.rs`, `src/routes/playback/prepare.rs` | Readying the next playlist entry's frame indexes and overlay artifacts in the cache folder while a viewer plays the entry before it: one background preparation for the backend, started from the entry a playback socket names (looked up in its own lease's Content) while `playback.prepare_next_video` is on, replaced by the next and ended with its socket |
| `src/disk_cache.rs` | Which files under the cache folder are ax-devil's own, found and removed for `clear-cache` |
| `src/thumbnails.rs` | The first-picture JPEGs of videos kept in the cache folder, keyed by the file's identity so a changed video is a new picture; the thumbnail route serves them |
| `src/access.rs` | Principals, route gates, the Local guard, security headers, socket admission limits |
| `src/share.rs` | Where other machines reach the backend: join-link base, the desktop's listener for them while a session is shared, the development-build refusal |
| `src/session/` | The Session socket's adapter: admission, liveness and close codes (`socket`, `exit`), the three lanes (`reader`), what each seat is sent (`writer`), wire-to-service conversion (`wire`), the launch queue, the session list, and `project.rs`, the only constructor of the Doc's wire form, which decides what each audience learns of it |
| `crates/<name>/` | One owning concept per crate (Scene model, catalog compiler, workspace, sources, ...), with its layer declared |
| `crates/foundation/` | Layer 1: Media Time, frame identity, the frame index types, decoded frames, cancellation and the user-facing error. No I/O |
| `crates/workspace/` | Layer 2: workspace items, recipes, Content (videos and playlists of entries and lanes) and the intake, and in `store` the workspace's state and its pure transitions (apply a command, complete a resolution), the hidden refs and the sidebar row projection; whoever holds a state decides where it lives. Resolving checks the files only; opening media is the viewer's job |
| `crates/scene/` | Layer 2: the Scene model decoders produce, the class filter policy, the Sample Item columns encoder, the grouped fields of an entity for inspection and Overlay Lookup (`matching`, the pure choice of the sample that belongs to a frame) |
| `crates/handler-api/` | Layer 2: what a handler is: the `Decoder` and `PlaylistResolver` traits, the `Input` and `SceneSink` they run against, the `Form` of options and settings both declare and validate, and the overlay and video references |
| `crates/handlers/` | Layer 2: the built-in decoders from overlay records to Scenes (`decoders`: MOT, UVG-VCM, ONVIF XML, CVAT, ADF) and playlist resolvers from settings to playlists of videos and overlay files (`resolvers`: folder pair, MOT Challenge), and the one registry of them with the installed plugins' handlers, with the swappable set of it that running work takes snapshots of |
| `crates/media/` | Layer 3: video file or image sequence to frame index and frames (FFmpeg); the only crate that links it ([README](../../crates/media/README.md)) |
| `crates/playback/` | Layer 3: what to decode and cache around each viewer's playhead, and its delivery; never depends on `media` ([README](../../crates/playback/README.md)) |
| `crates/workspace-files/` | Layer 3: how a workspace lives on disk: the version 2 file format, atomic save, recovery copies with their locks, and the recent workspaces list ([README](../../crates/workspace-files/README.md)) |
| `crates/sessions/` | The Session service: state, transitions, authorization, tickets and jobs; services layer, knows no message of the wire (the plain data it shares, such as a view, derives its own wire form) |
| `crates/scene-store/` | Layer 3: an overlay file decoded once into an on-disk artifact, shared by every viewer, with Scenes served from a bounded cache, and the file history that File scope queries ([README](../../crates/scene-store/README.md)) |
| `crates/catalogs/` | Layer 3: the catalog documents the app offers, the six built-in v4 documents and the `*.json` files of the user's folder, listed, polled for changes and served as text to the frontend, which compiles them; copies and deletes a user catalog on request ([README](../../crates/catalogs/README.md)) |
| `crates/overlays/` | Layer 3: a viewer's overlay data feed: Sample Items per source and Frame Maps per lane inside the sample window ([README](../../crates/overlays/README.md)) |
| `crates/plugins/` | Services layer: installed plugin programs and the handlers they provide (decoders and playlist resolvers): the manifest, the registry file and the process supervisor that feeds a decode's sink or a scan's and resolve's collector |
| `crates/camera/` | Services layer: everything that talks to an Axis camera (device API, network scan, RTSP video and metadata, MQTT analytics, DataHub samples, and the live connections' shared values), with no app-crate dependencies except `foundation`; only `live` may depend on it ([README](../../crates/camera/README.md)) |
| `crates/live/` | Adapters layer (it speaks the wire types of `api-types`): the adapter between `camera` and the app, and the only crate that depends on it. A feed per resolved camera (RTSP video, MQTT or DataHub overlay) shared by its viewers, the live gate and decoder, status, and shutdown that closes every connection; the network scan and the form's camera queries; what the sidebar asks a camera (`glance`: a snapshot for a hover card and a check of a stream's own saved login, each at most once per cooldown per key, only to a host the user started). Feature `fake` exports its test doubles |
| `crates/export/` | Services layer: an export job from frozen inputs to an encoded file, knowing no window, socket or Electron ([README](../../crates/export/README.md)) |
| `crates/settings/` | Layer 3: the settings file, its declared settings (each Personal or Server) and shortcuts ([README](../../crates/settings/README.md)) |
| `crates/api-types/` | Rust types sent to the frontend and the API version; each module lists its wire types in `visit_api_types`; `export-types` writes one file per module under `frontend/src/generated/types/`, re-exported by `generated/api.ts`, and fails when a type that derives `TS` in source is not listed; it also writes the settings and command tables to `frontend/src/generated/`, the shell version and reserved keys to `desktop/generated/api.json`, and the API version to the untracked `desktop/generated/api-version.json`; `session.rs` is the Session socket's wire protocol; plain data that already has an owning crate is not redefined here |
| `frontend/src/` | The UI. Components and their tests sit side by side |
| `frontend/src/stream/`, `lanes/`, `inspect/` | What a viewer's socket carries, each folder importing only the ones before it: the wire, worker, clock, frame buffer and data windows (`stream/`, no React); the lanes' model and menus (`lanes/`); the inspector's fields, File scope queries and appearances (`inspect/`) |
| `frontend/src/playback/`, `viewer/`, `viewers.ts` | The playback stream (its link, snapshot and lane data), renderer, viewport, transport and the inspector panel, the panes, viewers and playlist viewer, and the viewer registry keyed by Content kind |
| `frontend/src/tools/`, `search/` | The Objects list in Frame and File scope, and the search language both share (parser in `search/`; File scope's matching runs in `scene-store`) |
| `frontend/src/overlay/` | The WebGL painter and glyph atlas: Marks to quads, text through the atlas |
| `frontend/src/present/` | Overlay presentation per lane, from Sample Item and Lane Style to Marks, layout and picking ([README](../../frontend/src/present/README.md)) |
| `frontend/src/export/`, `frontend/src/render/`, `frontend/render.html`, `desktop/render.cjs` | The export dialog and the playback stream's export job; the Render Page that draws an export's pictures with the shared presentation code, and the hidden Electron that hosts it |
| `frontend/src/bench/`, `frontend/bench.html`, `desktop/bench.cjs` | The dev-only benchmark that times the real renderer in Electron (`make bench-browser`) |
| `frontend/src/resolvers/` | The resolver settings form, generated from the schema the backend sends, and the cancellable scan |
| `frontend/src/session/` | The session client: the socket and its store (the Doc, each seat's view, commands), cursors and presence |
| `frontend/src/shell/` | The window: title and activity bars, sidebar, the split area of viewer panes, and the sharing UI (`SessionsView`, `ShareControl`, `ConnectDialog`) |
| `frontend/src/client/` | The client layer: the credential this page holds and its role (`access.ts`), `fetch` for its own backend (`api.ts`), settings, commands, recents and the window bridge |
| `desktop/start.cjs`, `main.cjs` | The Electron entry (the shell, or the hidden renderer with `--render-host`) and the shell's lifecycle: which windows exist and how they move between local and remote |
| `desktop/args.cjs`, `backend.cjs`, `window.cjs`, `links.cjs`, `remote.cjs` | The command line (what opens a server, what the backend gets), the local backend process, windows with their trusted origin and IPC, launch links and the single instance, another server's address and its checks |
| `desktop/smoke*.cjs` | The smoke test and screenshot harness: `smoke-groups.cjs` lists the scenarios by group, `smoke.cjs` runs them, `smoke-kit.cjs` holds the page helpers, and one file per scenario |
| `assets/` | Packaged built-in resources: render catalogs, icons, banner. The app reads them in place and never writes them |
| `fixtures/` | Committed test inputs with their provenance and license ([README](../../fixtures/README.md)) |
| `scripts/` | Development and verification support; the Makefile is the entry point |
| `docs/` | Docs by the placement rule in [AGENTS.md](../../AGENTS.md#where-a-fact-belongs) |

## Placement rules

- Organize code by the concept that owns it, not by technical kind, and mirror that ownership in tests.
- A new Rust crate goes in `crates/`, inherits the workspace edition, Rust version and lints, takes shared dependency
  versions from the root `Cargo.toml`, and declares its layer.
- Rust unit tests live in the owning module's `#[cfg(test)]` block; tests across a crate's public interface go in that
  crate's `tests/` folder. Frontend tests are `*.test.tsx` beside the component.
- Generated files are never edited by hand: `frontend/src/generated/` and `desktop/generated/` come from
  `crates/api-types`. `frontend/dist/` and `target/` are build output.
- Add JavaScript dependencies with a filter, for example `pnpm --filter ax-devil-ui add <package>`, and commit the root
  `pnpm-lock.yaml` with the manifest. Every new Rust or JavaScript dependency passes the license allow list in
  `deny.toml`.
