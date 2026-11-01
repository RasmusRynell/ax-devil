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
| Local: `127.0.0.1`, for the desktop and development | The person at this machine | Everything; a Host guard and an Origin guard (scheme, host and port in full) keep other websites off |
| `serve`: another machine's server | Anyone who can reach it | The server password, join links and resume secrets |
| Desktop LAN: opened when a session starts sharing, closed once none is shared | Anyone who can reach it | Join links, resume secrets and playback tickets only; serves the frontend, `/api/info`, the session socket and the playback socket, nothing else |

Each request resolves to a principal with grants: Local has `preferences`, `configuration`, `sessions` and `files`;
Server (the password) has `configuration`, `sessions` and `files`; anyone else has none. A join link or resume
secret admits one seat of one Session and is checked by the Session socket, not by a grant, and a playback ticket
admits one stream and is checked by the playback socket. Only a principal with `files` can add videos from this
machine's files or list its handlers and scan its folders (`Who::Files`), and a watcher never learns a host path. The table in `src/routes/mod.rs` is the only place a
route is mounted: method, path, the listeners that serve it and who may call it. A method it does not list answers
405, and a conformance test calls every row as every kind of caller. The rules are in
[13](../rebuild/13-security-packaging-ops.md#sharing-must).

### Desktop shell

The shell starts the local backend on first use, or reuses a compatible one on its port. It can instead load another
server's own frontend, so client and server always run the same version. Before loading, it checks the server's
`/api/info` for this app and its shell interface version; a failure opens a native dialog (retry, return to local,
quit). A window trusts one origin: IPC answers only that origin's main frame, and navigation elsewhere is blocked. It
opens `ax-devil://` links (as an argument or a macOS `open-url`) after asking, and keeps one instance running; the
operating system's link registration waits for packaging.

Electron supplies Chromium, so rendering and media behave the same on every desktop instead of depending on the
system webview ([D0](../rebuild/14-build-order.md#d0-stack-choice)).

## Workspace

A window's Workspace is the Doc of its Session: the items (videos, playlists), each item's resolution, the hidden
refs and which items are open, as one pure `WorkspaceState` (`workspace-store`). It changes only by a `DocCommand`
the Session applies under its lock (add items, open and close a viewer, hide and unhide); resolving an item runs as
a job outside the lock and commits through it. A playlist resolves to Content with entries, each with its lanes; a
video is one entry whose id is the Content's id. Each seat is sent the Doc as `project` builds it for its audience,
sidebar rows included, so a watcher sees open items only, never a path or a failure's reason. Which entry a viewer
shows is decided in one place, `restrict`, and a stream is ticketed for one entry of one generation of the Content.

## Overlays

The backend supplies data and does the work every lane and session shares; the frontend does all presentation. An
overlay file is decoded once into an on-disk artifact that every viewer opening it shares (`scene-store`); memory holds
the sample keys and a bounded cache of Scenes. On the playback socket `overlays` sends two kinds of items. A Sample Item
is the immutable, per-source encoding of one sample: its latest observations as typed columns and a class mask from the
decoder's filter policy, with history, source data and debug data fetched on demand. A Frame Map is per lane: which
sample belongs to each frame, as `overlay-sync` selects it under the lane's Lane Policy (tolerance, sticky, whether the
lane is shown). Items and maps rank with the video frames by the same playhead rule and pass the same authority gate.
They travel inside the sample window: per source, the samples that subscribed lanes' Frame Maps assign to the frames of
the data window, never wider than the frame window and sized from a share of the viewer's frame budget. The backend
sends exactly those and the frontend evicts only others; the rule is implemented in Rust and TypeScript and tested
against one shared fixture. Revisions drop stale items, so there are no acknowledgements or per-seek epochs.

Per lane, `frontend/src/present/` compiles the lane's catalog document (served over the socket, built-in or user),
filters rows, evaluates Marks from the Sample Item, lays them out for the lane's cell with zoom, pan and aspect, and
paints them through one WebGL2 context per viewer. A lane's Lane Style (catalog, filters, theme, opacity, zoom, pan)
stays in the client and its Lane Policy in its socket; neither is shared with followers. Export and `catalog render`
will run the same code in an offscreen Electron page, and Rust will freeze their inputs and encode the frames (S6).
`catalog check` runs the compiler in Electron-as-Node from a bundle `make` builds with the frontend
(`desktop/generated/catalog-check.cjs`).

The Rust render path (`render-catalog`, `drawing`, the v3 catalog compile) is unused by playback and goes in slice S3.

## Layers

Dependencies point down only. Numbers are the layers of [02](../rebuild/02-architecture.md#layers-and-dependency-direction).

| # | Layer | Lives in | Check |
|---|-------|----------|-------|
| 1 | Foundation | Rust crates, `layer = "foundation"` | Workspace crates only; no server crates |
| 2 | Domain and contracts | Rust crates, `layer = "domain"` | same |
| 3 | Runtime services | Rust crates, `layer = "services"` | same |
| 3a | Event-loop adapters: routes, sockets, wire types | `crates/api-types` (`layer = "adapters"`) and the routes in `src/lib.rs` and `src/routes/` | May use the server crates |
| 4–7 | UI support, generic display, workflows, shell | `frontend/src/` | — |
| 8 | Entry and bootstrap | `src/main.rs` (backend), `desktop/` (window and backend lifetime) | The root crate holds the routes and `main.rs`, so it declares `layer = "entry"` |

Every crate declares its layer in `[package.metadata.ax-devil]`; `scripts/check-architecture.cjs` (part of
`make check`) rejects a crate that depends on a higher layer, a layer 1–3 crate that depends on the HTTP server, and the
pairs it lists (for example `playback` on `media`).
The frontend's layers 4–7 get their import rules when the first of those modules land.

## Runtime

- **Development** (`make web`, `make desktop`): Vite serves the frontend with live reload and proxies `/api` to a
  debug backend that `scripts/dev.cjs` rebuilds and restarts on Rust changes. The command that starts a service owns
  it; a second command reuses running services that report a compatible API version.
- **Release** (`make build`): the backend embeds the built frontend and serves it. The desktop shell starts the
  release binary when it needs one, waits for its readiness line, and stops it on quit. A development build serves no
  frontend, so it refuses to open a listener other machines would use.
