# Module Map

Where code and files belong. Layers and the dependency rule are in the [overview](overview.md#layers).

| Path | Owns |
|------|------|
| `src/main.rs`, `src/cli.rs`, `src/runtime.rs` | Backend startup and shutdown and the command line (the local backend, `local --video`, `serve`, `app`); prints the readiness line the shell and supervisor wait for |
| `src/lib.rs` | The backend's two entry points (`app`, `lan_app`), the state every route reaches, settings and info handlers, the embedded frontend |
| `src/routes/` | The route table and the playback WebSocket (redeems a ticket into a stream lease and enforces its authority): adapters over the services, no logic |
| `src/video_sources.rs`, `src/video_cache.rs` | Where `media` meets `playback` (the decoder adapter, opening viewers on files) and how the `playback.video_cache` setting sizes the cache |
| `src/access.rs` | Principals and grants, the Local guard, security headers, socket admission limits |
| `src/share.rs` | Where other machines reach the backend: join-link base, the desktop's listener for them while a session is shared, the development-build refusal |
| `src/session/` | The Session socket's adapter: admission, liveness and close codes (`socket`, `exit`), the three lanes (`reader`), what each seat is sent (`writer`), wire-to-service conversion (`wire`), the launch queue, the session list, and `project.rs`, the only constructor of the Doc's wire form, which decides what each audience learns of it |
| `crates/<name>/` | One owning concept per crate (Scene model, catalog compiler, workspace, sources, ...), with its layer declared |
| `crates/foundation/` | Layer 1: Media Time, frame identity, the frame index types, decoded frames, cancellation and the user-facing error. No I/O |
| `crates/workspace/` | Layer 2: workspace items, recipes, Content and the intake. Resolving checks the file only; opening media is the viewer's job |
| `crates/media/` | Layer 3: video file to frame index and frames (FFmpeg); the only crate that links it ([README](../../crates/media/README.md)) |
| `crates/playback/` | Layer 3: what to decode and cache around each viewer's playhead, and its delivery; never depends on `media` ([README](../../crates/playback/README.md)) |
| `crates/workspace-store/` | Layer 3: the workspace's state and its pure transitions (apply a command, complete a resolution); whoever holds a state decides where it lives |
| `crates/sessions/` | The Session service: state, transitions, authorization, tickets and jobs, generic over the cursor type; services layer, knows no message of the wire (the plain data it shares, such as a view, derives its own wire form) |
| `crates/api-types/` | Rust types sent to the frontend and the API version; `export-types` writes them to `frontend/src/generated/api.ts` and the version to `desktop/generated/api.json`; `session.rs` is the Session socket's wire protocol; plain data that already has an owning crate is not redefined here |
| `frontend/src/` | The UI. Components and their tests sit side by side |
| `frontend/src/playback/`, `viewer/`, `viewers.ts` | The playback session (clock, frame buffer, renderer), the viewer host, and the viewer registry keyed by Content kind |
| `frontend/src/session/` | The session client: the socket and its store (the Doc, each seat's view, commands), cursors and presence |
| `frontend/src/shell/` | The window: title and activity bars, sidebar, and the sharing UI (`SessionsView`, `ShareControl`, `ConnectDialog`) |
| `frontend/src/access.ts`, `api.ts` | The credential this page holds and its grants; `fetch` for its own backend |
| `desktop/main.cjs` | The shell's lifecycle: which windows exist and how they move between local and remote |
| `desktop/args.cjs`, `backend.cjs`, `window.cjs`, `links.cjs`, `remote.cjs` | The command line (what opens a server, what the backend gets), the local backend process, windows with their trusted origin and IPC, launch links and the single instance, another server's address and its checks |
| `desktop/smoke.cjs`, `smoke-kit.cjs`, `smoke-video.cjs`, `smoke-sharing.cjs`, `smoke-remote.cjs` | The smoke test and screenshot harness: the main scenario and its runner, shared page helpers, and the video, sharing and remote-server scenarios |
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
