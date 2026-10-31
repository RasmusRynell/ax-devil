# Architecture Overview

ax-devil is a Rust backend and a React frontend. The desktop app is an Electron window that starts the backend and
loads the frontend from it; the same frontend also runs in a browser during development. Where code goes is in the
[module map](module-map.md); the rules it must keep are in the [invariants](../domain/invariants.md).

Until parity, the layer contents, seams and runtime guarantees are specified in
[02](../rebuild/02-architecture.md) and [03](../rebuild/03-runtime.md); each moves here as it is built.

## Processes

| Process | Owns |
|---------|------|
| Backend (`ax-devil`, Rust) | All application state and work: domain, sources, decoding, rendering preparation, files, settings |
| Frontend (React, in Chromium) | The UI: a projection of backend state that sends user intent back |
| Desktop shell (Electron) | Windows and the backend process's lifetime; no application features |

The frontend talks to the backend over HTTP and WebSockets on `127.0.0.1`. Types that cross that boundary are Rust
types in `crates/api-types`, exported to TypeScript; the frontend never declares its own copy.

Electron supplies Chromium, so rendering and media behave the same on every desktop instead of depending on the
system webview. The prototype this stack comes from found the Linux WebKitGTK webview crashing during video support
detection.

## Layers

Dependencies point down only. Numbers are the layers of [02](../rebuild/02-architecture.md#layers-and-dependency-direction).

| # | Layer | Lives in | Check |
|---|-------|----------|-------|
| 1 | Foundation | Rust crates, `layer = "foundation"` | Workspace crates only; no server crates |
| 2 | Domain and contracts | Rust crates, `layer = "domain"` | same |
| 3 | Runtime services | Rust crates, `layer = "services"` | same |
| 3a | Event-loop adapters: routes, WebSocket notifications, wire types | `src/` routes, `crates/api-types`, `layer = "adapters"` | May use the server crates |
| 4–7 | UI support, generic display, workflows, shell | `frontend/src/` | — |
| 8 | Entry and bootstrap | `src/main.rs` (backend), `desktop/` (window and backend lifetime) | — |

Every crate declares its layer in `[package.metadata.ax-devil]`; `scripts/check-architecture.cjs` (part of
`make check`) rejects a crate that depends on a higher layer, or a layer 1–3 crate that depends on the HTTP server.
The frontend's layers 4–7 get their import rules when the first of those modules land.

## Runtime

- **Development** (`make web`, `make desktop`): Vite serves the frontend with live reload and proxies `/api` to a
  debug backend that `scripts/dev.cjs` rebuilds and restarts on Rust changes. The command that starts a service owns
  it; a second command reuses running services that report a compatible API version.
- **Release** (`make build`): the backend embeds the built frontend and serves it. The desktop shell reuses a
  compatible backend on its port or starts the release binary, waits for its readiness line, and stops it on quit.
