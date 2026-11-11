# 14 Build Order and Decisions

Delivery framing is in the [README](README.md#delivery). This file holds D0, the decisions taken and what is left; the
open ones are in [15](15-open-decisions.md).

## D0: stack choice

**Taken at the start of the rewrite: the stack of the owner's Rust prototype**: a Rust backend (axum on tokio)
holding all state and work; a React and TypeScript frontend built, linted, formatted and tested with Vite+ (Vitest,
Oxlint, Oxfmt) and styled with Tailwind; an Electron shell that owns windows and the backend process; one Cargo and
one pnpm workspace; Make as the task runner. Reasons: the prototype showed one React UI serving browser and desktop
over HTTP and WebSockets from one backend; Chromium from Electron instead of the system webview (WebKitGTK
crashed during video support detection); an RTSP session with `retina`; a Python decoder as a subprocess speaking
versioned JSON lines; Electron on Xvfb with screenshots. It meets the typing, format, lint and
dependency-check criterion (strict TypeScript, rustfmt, Oxfmt, Clippy, Oxlint, `scripts/check-architecture.cjs`).

Decode and seek is decided ([below](#decisions-taken-for-offline-video)). Spikes still owed:

| Spike | What the prototype did not show |
|-------|---------------------------------|
| Live capture time | Per-frame capture time from RTSP (ONVIF replay extension or RTCP sender reports) for video and the ONVIF metadata track with `retina`; MQTT; WebSocket with Digest (D14) |
| One surface, two targets | D22 S0: 4 lanes of 1000 and of 5000 objects presented within the main-thread budget ([03](03-runtime.md#performance)) on a real GPU; then the same code offscreen in Electron at native size for export |
| Plugins | The D29 runtime: a file decoder and a resolver as plugins (a payload decoder with live sources); the D4 schema against both built-in resolvers |
| Platform | Fractional HiDPI and the accessibility tree in Electron; Windows and macOS builds (D2) |

## Decisions taken for the foundation

Where the prototype and this playbook differed or were silent, the foundation took the simplest
prototype-matching option; the owner can revisit each.

| Decision | Reason |
|----------|--------|
| Make targets keep the prototype's verbs plus `check-fast` and `format`. No `sync`; `run`/`run-dev` are `desktop`/`web` | One set of verbs, no setup step |
| `scripts/cargo` runs cargo through the user's rustup, or installs a checkout-local one into `.cargo-cache/`; a cargo without rustup is ignored because it would not honor the pinned `rust-toolchain.toml` | A clean clone builds with no setup step |
| The logging gate is lints (Clippy `print_stdout`, `print_stderr`, `dbg_macro`; Oxlint `no-console`, off for `scripts/` and `desktop/`), not a script | The tools already enforce it |
| `scripts/` and `desktop/` JavaScript is linted with type-aware rules and formatted, not type-checked | Type-checking CommonJS needs Node and Electron types and a second TypeScript config; add them when it grows |
| The architecture check reads `cargo metadata`; each crate declares `[package.metadata.ax-devil] layer`. Frontend import rules come with frontend modules | The smallest check that enforces layer direction and keeps layers 1–3 free of the HTTP server |
| License gate: `cargo-deny` for crates and `scripts/check-js-licenses.cjs` for npm packages, one allow list in `deny.toml` | [13](13-security-packaging-ops.md#licensing) asks for a check of every dependency |
| Stale generated files fail `make check`, which compares the tree before and after its own run | The build regenerates them locally; the check covers all of them |
| The `rewrite` branch is the whole rewrite and the only PR that merges to `main`, at parity. Later work lands on it or as PRs with base `rewrite`; CI runs for both branches | `main` keeps the Python app until one reviewed merge replaces it |
| No pre-commit hooks; `make check-fast` locally and CI enforce the gates | Hooks need a setup step per clone |
| Every suite runs through `scripts/isolated`; it guards the `ax-devil` folders under the XDG config, data, cache and state homes (and the prototype's `~/.ax_devil`). Electron's data goes to `ax-devil-chromium` in the cache home, outside the guard | Every path the app uses resolves in `src/paths.rs` to one of those folders; whoever adds one updates both |
| Custom window frame (D7): a frameless Electron window whose in-page top bar is the title bar (menus, drag region, window controls through a minimal preload; absent in a browser) | The owner's window manager draws no title bar, so a native frame could not move or close |
| D10: accessibility target is WCAG 2.x AA contrast (4.5:1 text, 3:1 large text and boundaries), every action reachable by keyboard, accessible names on icon-only controls | The tokens are contrast-checked in both themes when built, the write-ui skill states the rule; the accessibility-tree pass comes with polish |
| Zoom is the browser's. Electron zooms with Ctrl+=, Ctrl+- and Ctrl+0, remembered per host; layouts must fit the window minimum and zoom up to 200% | A web app inherits zoom. `ui.text_size` (System to Larger) stays by owner decision, a separate choice that leaves zoom alone |
| No root `ARCHITECTURE.md`; the prototype's content lives in `docs/architecture/overview.md` | One home |
| Development ports 7330 (backend) and 7331 (frontend); the API version has one source in `crates/api-types`; the shell reads it from the generated `desktop/generated/api.json` | Distinct from the prototype's ports, so both run; one version source |
| One `ci.yml`: `make check` once on Ubuntu, and a package-and-smoke job per OS. No test runs on macOS or Windows; no lowest-dependency job (Cargo needs nightly) | The gate runs once; the other systems only prove the packaged app starts |

## Decisions taken for offline video

These were taken with the owner when offline video was built, who can revisit each. The rules they led to live with their modules
(`crates/media/README.md`, `crates/playback/README.md`, `docs/domain/invariants.md#offline-delivery`); the other
deviations from the reference are listed by [parity](../parity.md).

| Decision | Reason |
|----------|--------|
| FFmpeg decodes natively in the backend with `ffmpeg-next` (WTFPL, added to `deny.toml`) linking the system libraries, FFmpeg 6.1 to 9. Frames reach the frontend as raw I420 (NV12 or RGBA if the source needs it), converted by a WebGL2 shader. JPEG and WebCodecs were rejected; hardware decode is not built | `rsmpeg` 0.18 fails against FFmpeg 9.0.2; `ffmpeg-next` 9.0.0 passes the media suite against 9.0.2 and Ubuntu 24.04's 6.1.1. Spike (real GPU): raw I420 plays 4K30 with no drops; JPEG q90 dropped 13 of 300 frames at 4K30; software WebCodecs decodes 4K H.264 at 37 fps and has no HEVC on Linux. D6 decides the bundled FFmpeg build (LGPL, dynamic). |
| Offline playback's clock and window: the rule is in `docs/domain/invariants.md#offline-delivery`; a viewer sends its window (`Want`: playhead, frames ahead and behind, direction), which replaces play, pause and speed messages | Presentation must follow the display refresh, immune to socket jitter; frames are already buffered. Lanes share one primary clock; live needs none and export is clock-free in the backend |
| Overlay data arrives as its own messages, joined with the frame in the frontend (items and maps: D26) | Changing a catalog or filter never resends pixels |
| D19: a viewer shows exactly one video, so the playback socket and the frame envelope are per viewer and carry no lane; only overlay messages name a source or lane. Two videos side by side are two viewers with no shared clock | Lanes share every frame, so a frame per lane would multiply bytes; one socket per viewer keeps the clock frontend-owned and control lane-free |
| Cache root `$XDG_CACHE_HOME/ax-devil/` (`~/.cache/ax-devil/`), or `--cache-dir` | Resolved in `src/paths.rs`, guarded by `scripts/isolated` |
| D9 budgets: backend cache Auto is 25% of the memory available at startup, at least 256 MiB (1 GiB where it cannot be read, off Linux); frontend buffer 512 MiB per window, shared equally by its panes `[AI]` (one pane: about 170 frames at 1080p, 43 at 4K); 2 decode workers per source; FFmpeg threads (1 to 8 each) share one budget of the machine's cores. Sample data takes 10% of the viewer's frame budget and is evicted by playhead distance, before frames. Target: resident memory at most the cache allowance plus 600 MB at 4K (not measured) | The backend cache is a share of free memory and adapts to the machine; the frontend buffer fixes the window per video and pane count |
| D3: one process. Decode workers are threads of the backend | A thread stops at a frame boundary and shares the cache pool; no helper process |
| D21: settings are one flat, sparse, versioned file in the XDG config folder ([mechanics](../../crates/settings/README.md)); window state lives in the desktop shell's own file, apart from the settings | An unreadable file stops startup rather than lose choices silently |

## Decisions taken for the overlay pipeline

The owner approved D22–D30 together, then D31–D36. Rules:
`docs/domain/invariants.md`; shape: `docs/architecture/overview.md#overlays`.

| Decision | Reason |
|----------|--------|
| D22: the backend supplies data only (decode, parse, store, index, frame map, selection, query) and does all work shared across sessions and lanes. All presentation (catalog and style evaluation, filters, label layout, glyph atlas, zoom, pan, theme, marks, picking) is TypeScript run per lane on the browser main thread. No WebAssembly, no native second renderer | Sessions and lanes view the same data differently, so per-view work must not cost the backend; the CSP forbids WASM |
| D23: export runs the same presentation code in an offscreen Electron page, `catalog check` in Electron-as-Node. Rust freezes the inputs and encodes frames with source timestamps. D6 decides the format and encoder when export is built; the default is MP4/H.264 through the linked FFmpeg | One implementation for screen and export |
| D24: the core use case is one video with two different overlay sources, each lane with its own catalog, filter and theme (zoom and pan are the viewer's, D31); the same source in two lanes works too: an overlay may repeat in an item, its label optional, a repeat numbered ` (2)`. Different videos stay out (D19) | Lane style is client state, so a shared source costs nothing on the wire; lane labels stay unique |
| D25: catalog schema v4 replaces v3: flat rules plus ordered layers, label placement lists, one expression language for styles and filters, no `extends`. Layer order replaces the fixed shapes, text, labels order. Built-in and user catalogs are served over the socket. The format spec is yet to be written | The frontend compiles catalogs, so styles, filters and draw order need one language it evaluates alone |
| D26: Scene stays the store's data model. A Sample Item is the immutable encoding of one sample, keyed by source revision and sample; a Frame Map per lane assigns samples to frames. The backend owns tolerance, sticky and sequence selection; revisions replace per-seek epochs; colour lists are a typed Scene value; class filters use a backend-computed class mask | Lanes and viewers share a source's items and differ only in their maps; the decoder's class rules stay in Rust |
| D27: a lane never draws another sample's marks. During play it shows a loading state and the video is not held; after a seek or step a frame waits at most about 250 ms; export always waits. Supersedes "a frame is shown only when every visible lane holds its overlay" | A slow backend must neither freeze playback nor mark the wrong frame |
| D28: live uses a bounded deadline (jitter buffer); the gate covers all lanes sharing the viewer's video; the start value is 1.5 s until camera skew is measured | Late metadata must neither stall video nor mark the wrong frame |
| D29: ingest runs plugin processes (any language, JSON Lines; protocol 3 ops decode, scan, resolve) described by one manifest listing typed handlers (decoder, playlist resolver) and owning their environment through `command`, with an allowlisted environment, drained stderr, a 16 MiB line cap and process-group kill; a failure marks the lane incomplete and cache identity includes the script hash. The host groups equal-timestamp records and sorts when ingest ends. Settles D1: no in-process Python bridge | A process boundary isolates and kills; only the host writes the store |
| D30: file-scope queries stay a separate restricted backend query (a track that was car, then bus, matches both); the whole-file filter (IN-02) is built in S8 | Sample Items hold only the latest observation, so a file-wide question cannot use them |
| D31: one viewport per viewer, shared by its lanes: zoom 1 to 10 and pan, held by the client; the window decides aspect. Image zoom keys are `=`, `-` and `0` | IN-05 syncs zoom across lanes; Ctrl+=, Ctrl+- and Ctrl+0 are the page zoom's |
| D32: page zoom never scales an overlay's pixel lengths: layout divides them by Electron's zoom factor (a plain browser has none, so there they follow the page) | A label's size on screen is the catalog's, whatever the page zoom |
| D33: user catalogs are the `*.json` files directly in `storage.catalog_dir`, else `$XDG_DATA_HOME/ax-devil/catalogs`, identified as `user:<file name>`; schema 4 has no `id` key. A poller reloads them, reading a file only after one unchanged interval; a lane keeps its last good catalog | Location is identity, so copies cannot collide; polling needs no dependency and works on network shares; a half-written save never reaches a lane |
| D34: Lane Style (catalog, theme, hidden features, class toggles, filter expression, sticky opacity) stays in the client; tolerance, sticky on/off and timeout, and visibility are the Lane Policy sent to the backend. Apply to all copies a lane's settings but visibility to the viewer's other lanes. The default catalog is the Personal setting `overlays.default_catalog`; `overlays.object_details` turns hover, chip and selection off | The backend needs only what selects samples; the default is how this user's app behaves |
| D35: a lane whose Lane Policy changes is blank until its new Frame Maps arrive, replacing the reference's "old analysis stays shown" ([07](07-interactions.md)) | By D27 a lane never draws a sample its policy no longer selects |
| D36: picking is frontend only, over the lane's filtered rows: the smallest box under the pointer; hidden overlays stay pickable. Selection is client view state by entity id, kept across seeks and shown in every lane of its source. The inspector shows the Sample Item's summary and asks the backend once (`Inspect`: Found, Absent or Unavailable) for the rest, gated like data (nothing for a follower outside its window) | Sample Items hold only the hot columns; a follower must not learn what its presenter no longer shows |

## Milestones

M0 to M8 scheduled the work and are built; status is in [parity](../parity.md), not milestone order. Left: M9,
packaging and release with the S rows the owner selects (benchmarks, release check, soak). The rewrite merges when
every M row is done or dropped by the owner in writing. Installing and upgrading the release artifact are release
gates though the reusable DT-05 tooling is S, and every S capability needs an explicit disposition before the
rewrite claims all Python use cases.
