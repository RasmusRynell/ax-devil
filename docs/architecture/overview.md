# Architecture Overview

ax-devil is a Rust backend and a React frontend. The desktop app is an Electron window that starts the backend and
loads the frontend from it; the same frontend also runs in a browser during development. Where code goes is in the
[module map](module-map.md); the rules it must keep are in the [invariants](../domain/invariants.md).

Until parity, the layer contents, seams and runtime guarantees are specified in
[02](../rebuild/02-architecture.md) and [03](../rebuild/03-runtime.md); each moves here as it is built.

## Processes

| Process | Owns |
|---------|------|
| Backend (`ax-devil`, Rust) | Application state and work: domain, sources, decoding, rendering preparation, files, settings, [sessions](../../crates/sessions/README.md) |
| Frontend (React, in Chromium) | The UI: a projection of backend state that sends user intent back, and the owner of its own view state |
| Desktop shell (Electron) | Windows, one trusted origin each, and the local backend's lifetime; no application features |

The frontend talks to the backend that served it over HTTP and WebSockets: on `127.0.0.1` for a local backend, on
the server's own address for another machine's. Types that cross that boundary are Rust types exported to TypeScript;
the frontend never declares its own copy. Plain data is defined once, in the crate that owns it, with `serde` and
`ts-rs` derives (layers 1-3 may use both); `crates/api-types` holds the socket protocols and the projections that
decide what an audience sees. The one hand-parsed format is the binary playback
envelope: its constants are exported and its layout is in `api_types::playback`.

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
machine's files, and a watcher never learns a host path. The table in `src/routes/mod.rs` is the only place a
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

## Layers

Dependencies point down only. Numbers are the layers of [02](../rebuild/02-architecture.md#layers-and-dependency-direction).

| # | Layer | Lives in | Check |
|---|-------|----------|-------|
| 1 | Foundation | Rust crates, `layer = "foundation"` | Workspace crates only; no server crates |
| 2 | Domain and contracts | Rust crates, `layer = "domain"` | same |
| 3 | Runtime services | Rust crates, `layer = "services"` | same |
| 3a | Event-loop adapters: routes, WebSocket notifications, wire types | `crates/api-types` (`layer = "adapters"`) and the routes in `src/lib.rs` and `src/routes/` | May use the server crates |
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
