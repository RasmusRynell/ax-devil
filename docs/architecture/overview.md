# Architecture Overview

ax-devil is a Rust backend and a React frontend. The desktop app is an Electron window that starts the backend and
loads the frontend from it; the same frontend also runs in a browser, locally or against a shared server. Where code goes is in the
[module map](module-map.md); the rules it must keep are in the [invariants](../domain/invariants.md).

Until parity, the layer contents, seams and runtime guarantees are specified in
[02](../rebuild/02-architecture.md) and [03](../rebuild/03-runtime.md); each moves here as it is built.

## Processes

| Process | Owns |
|---------|------|
| Backend (`ax-devil`, Rust) | Application state and work: domain, sources, decoding, overlay data, files, settings, [sessions](../../crates/sessions/README.md) |
| Frontend (React, in Chromium) | The UI: a projection of backend state that sends user intent back, the owner of its own view state, and overlay presentation per lane |
| Desktop shell (Electron) | Windows, one trusted origin each, and the local backend's lifetime; no application features |

The frontend talks to the backend that served it over HTTP and WebSockets: on `127.0.0.1` for a local backend, on
the server's own address for another machine's. Types that cross that boundary are Rust types exported to TypeScript;
the frontend never declares its own copy. Plain data is defined once, in the crate that owns it, with `serde` and
`ts-rs` derives (layers 1-3 may use both); `crates/api-types` holds the socket protocols and the projections that
decide what an audience sees. The hand-parsed formats are the binary playback
envelope (its constants are exported and its layout is in `api_types::playback`) and the Sample Item
columns, written by `scene::serial::columns` and read by `frontend/src/present/columns.ts`.

How offline playback divides its work between them is in the [invariants](../domain/invariants.md#offline-delivery).

### Listeners and principals

| Listener | Reached by | Admits |
|----------|------------|--------|
| Local: `127.0.0.1`, for the desktop and development | The person at this machine: the Owner, with Full access | Everything; a Host guard and an Origin guard (scheme, host and port in full) keep other websites off |
| `serve`: another machine's server | Anyone who can reach it | The server password (the Owner without Full access), join links and resume secrets |
| Desktop LAN: opened when a session starts sharing, closed once none is shared | Anyone who can reach it | Join links, resume secrets and playback tickets only; serves the frontend, `/api/info`, `/api/client-errors` (a page reports an uncaught error to the log), the session socket and the playback socket, nothing else |

Each request resolves to an Owner (with or without Full access) or to no credential; [access](access.md) says who may do
what. A join link or resume secret admits one seat of one Session and is checked by the Session socket, and a playback
ticket admits one stream and is checked by the playback socket. A watcher never learns a host path. The table in
`src/routes/mod.rs` is the only place a route is mounted: method, path, the listeners that serve it and who may call it. A method it does not list answers
405, and a conformance test calls every row as every kind of caller. The rules are in
[13](../rebuild/13-security-packaging-ops.md#sharing-must).

The listener also decides how frames travel (`src/routes/playback/wire.rs`): decoded planes on the Local listener,
compressed frames on the others, which the playback socket's worker decodes before the session sees them. However
many viewers are sent a frame, it is compressed once, on the `playback::SharedFrame` they all hold.

### Desktop shell

The shell starts the local backend on first use, or reuses a compatible one on its port. It can instead load another
server's own frontend, so client and server always run the same version. Before loading, it checks the server's
`/api/info` for this app and its shell interface version; a failure opens a native dialog (retry, return to local,
quit). A window trusts one origin: IPC answers only that origin's main frame, and navigation elsewhere is blocked. It
opens `ax-devil://` links (as an argument or a macOS `open-url`) after asking, and keeps one instance running: a later
launch's server, link or command (`local --video`, `playlist`, `open`) reaches it, and a command opens in a new window
of the running app, read in the folder it was typed in (`--hand-off`, `/api/launch`). A packaged app registers the
`ax-devil://` scheme with the system.

Electron supplies Chromium, so rendering and media behave the same on every desktop instead of depending on the
system webview ([D0](../rebuild/14-build-order.md#d0-stack-choice)).

## Workspace

A window's Workspace is the Doc of its Session: the items (videos, playlists), each item's resolution, the hidden
refs and which items are open, as one pure `WorkspaceState` (`workspace-store`). It changes only by a `DocCommand`
the Session applies under its lock (add items, open and close a viewer, hide and unhide); resolving an item runs as
a job outside the lock and commits through it. The runner records the files each item was read from (a video's
overlays, a playlist resolver's folders, the overlays its Content names), and a poll in `sessions` reports a change:
an item nobody has open resolves again, and an open one only counts the change, which its viewer offers to reload,
because resolving again would end the stream. A playlist resolves to Content with entries, each with its lanes; a
video is one entry whose id is the Content's id. Each seat is sent the Doc as `project` builds it for its audience,
sidebar rows included, so a watcher sees open items only, never a path or a failure's reason. Which entry a viewer
shows is decided in one place, `restrict`, and a stream is ticketed for one entry of one generation of the Content.
Opening, saving and recovering a workspace is `src/session/files/` over `workspace-files`: it reads and writes
files, then tells the Session what happened through `Replace` and `MarkSaved`, and the store only compares the
workspace with what was last saved.

## Overlays

The backend supplies data and does the work every lane and session shares; the frontend does all presentation. An
overlay file is decoded once into an on-disk artifact that every viewer opening it shares (`scene-store`); memory holds
the sample keys and a bounded cache of Scenes. On the playback socket `overlays` sends two kinds of items. A Sample Item
is the immutable, per-source encoding of one sample: its latest observations as typed columns and a class mask from the
decoder's filter policy; the rest of an entity's fields (history, source data, debug data) come from one `Inspect` request, answered only for a sample the viewer holds and gated like data; `InspectFields` is the same for the leaves of every object at the paths a Frame scope search names. A Frame Map is per lane: which
sample belongs to each frame, as `overlay-sync` selects it under the lane's Lane Policy (match basis, tolerance, sticky, whether the
lane is shown). Items and maps rank with the video frames by the same playhead rule and pass the same authority gate.
They travel inside the sample window: per source, the samples that subscribed lanes' Frame Maps assign to the frames of
the data window, never wider than the frame window and sized from a share of the viewer's frame budget. The backend
tells the viewer its data window and every sample it stops holding, within a byte budget the viewer sets when it
opens; the viewer evicts only on that word. Revisions drop stale items, so there are no acknowledgements or per-seek
epochs.

File scope lists a lane's whole file, which no window holds: `scene-store` builds a history at ingest, and the socket
answers one query per lane (a newer one cancels the older) with one page of rows, placed on the video under the lane's
Lane Policy. Replies name their revisions and no frame, so the gate never holds them; only a viewer with its own
authority is answered, and it needs the ingest ended.

Per lane, `frontend/src/present/` compiles the lane's catalog document (served over the socket, built-in or user; a
changed user file is pushed to the sockets that asked for it), filters rows, evaluates Marks from the Sample Item, lays
them out for the lane's cell, and paints them through one WebGL2 context per viewer. A lane's Lane Style (catalog,
theme, hidden features, filters, sticky opacity) stays in the client and its Lane Policy in its socket; neither is
shared with followers; an export carries the style frozen. The viewer's viewport and selection are client state;
picking runs over the lane's filtered rows. Export runs the same code in the Render Page, a hidden
offscreen Electron page (`desktop/render.cjs`, no display) that Rust talks to over a private loopback socket: Rust
freezes the job's inputs, sends the page each frame's picture and Sample Items, and encodes the pixels it returns.
`catalog check` runs the compiler in Electron-as-Node from a bundle `make` builds with the frontend
(`desktop/generated/catalog-check.cjs`).

## Layers

Dependencies point down only. Numbers are the layers of [02](../rebuild/02-architecture.md#layers-and-dependency-direction).

| # | Layer | Lives in | Check |
|---|-------|----------|-------|
| 1 | Foundation | Rust crates, `layer = "foundation"` | Workspace crates only; no server crates |
| 2 | Domain and contracts | Rust crates, `layer = "domain"` | same |
| 3 | Runtime services | Rust crates, `layer = "services"` | same |
| 3a | Event-loop adapters: routes, sockets, wire types | `crates/api-types` (`layer = "adapters"`) and the routes in `src/lib.rs` and `src/routes/` | May use the server crates |
| 4–7 | UI support, generic display, workflows, shell | `frontend/src/` | Folder layers in `FRONTEND_LAYERS` |
| 8 | Entry and bootstrap | `src/main.rs` (backend), `desktop/` (window and backend lifetime) | The root crate holds the routes and `main.rs`, so it declares `layer = "entry"` |

Every crate declares its layer in `[package.metadata.ax-devil]`; `scripts/check-architecture.cjs` (part of
`make check`) rejects a crate that depends on a higher layer, a layer 1–3 crate that depends on the HTTP server, and the
pairs it lists (for example `playback` on `media`).
Each `frontend/src` folder and root-level file sits in one layer of `FRONTEND_LAYERS` in `scripts/check-frontend-layers.cjs`, lowest first; a file imports only its own layer or below, so code two layers share moves to the lower one, and UI two features share to `frontend/src/kit/`.

## Runtime

- **Development** (`make web`, `make desktop`): Vite serves the frontend with live reload and proxies `/api` to a
  debug backend that `scripts/dev.cjs` rebuilds and restarts on Rust changes. The command that starts a service owns
  it; a second command reuses running services that report a compatible API version.
- **Release** (`make build`): the backend embeds the built frontend and serves it. The desktop shell starts the
  release binary when it needs one, waits for its readiness line, and stops it on quit. A development build serves no
  frontend, so it refuses to open a listener other machines would use.
