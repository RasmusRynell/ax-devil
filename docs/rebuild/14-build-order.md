# 14 Build Order and Decisions

Delivery framing is in the [README](README.md#delivery). This file holds the stack criteria, D0, milestones and the
decisions taken; [15](15-open-decisions.md) holds the open ones.

## Stack criteria

| Criterion | Ref |
|-----------|-----|
| Frame-accurate decode and seek, long-GOP H.264/H.265, 4K; Media Time from container timestamps, variable frame rate | [04](04-domain-rules.md#neutral-types-to-fix-early) |
| RTSP client exposing per-frame device capture time for video and metadata tracks (*current:* ONVIF replay header extension, requested with `onvifreplayext=1` in `data_sources/live/rtsp_source.py`; RTCP sender-report NTP mapping is the generic alternative); MQTT; WebSocket with Digest | LV-04, D14 |
| GPU video plus custom overlay drawing in one surface, and the same semantics offscreen at native size | [06](06-ui-ux.md#overlay-drawing) |
| Concurrency with a UI-thread hand-off and executor-bound destruction | [03](03-runtime.md#concurrency-guarantees-must) |
| Live theme changes, fractional HiDPI, accessibility API | [06](06-ui-ux.md) |
| Offscreen or private-display rendering, toolkit-level test input, window capture | [09](09-testing.md#isolation-must) |
| Strict typing, formatter, linter, dependency-rule checker | [10](10-quality-gates.md) |
| A plugin story compatible with D1 and D6 | [05](05-formats-and-extensions.md#plugin-runtime) |

## D0: stack choice

Run throwaway spikes against the criteria: decode and seek, RTSP/ONVIF capture
time, offscreen rendering of video plus overlays, plugin bridge feasibility if D1 keeps a bridge, and the D4 schema
against both built-in resolvers. The owner signs D0 on the results. Spike code is not committed to the new tree.

**Taken at the start of the rewrite: the stack of the owner's Rust prototype** (new tree): a Rust backend (axum on tokio)
holding all state and work; a React and TypeScript frontend built, linted, formatted and tested with Vite+ (Vitest,
Oxlint, Oxfmt) and styled with Tailwind; an Electron shell that owns windows and the backend process; one Cargo and
one pnpm workspace; Make as the task runner. Reasons: the prototype already showed one React UI serving browser and
desktop over HTTP and WebSockets from one Rust backend; Chromium from Electron instead of the system webview (Linux
WebKitGTK crashed during video support detection); an RTSP session with `retina`; a Python decoder as a subprocess
speaking versioned JSON lines; and Electron driven on Xvfb with window screenshots. It meets the typing, formatter, linter and
dependency-check criterion (Rust, strict TypeScript, rustfmt and Oxfmt, Clippy and Oxlint,
`scripts/check-architecture.cjs`).

Decode and seek is spiked and decided ([below](#decisions-taken-for-offline-video)). Spikes still owed before the owner signs:

| Spike | What the prototype did not show |
|-------|---------------------------------|
| Live capture time | Per-frame device capture time from RTSP (ONVIF replay extension or RTCP sender reports) for video and the ONVIF metadata track with `retina`; MQTT; WebSocket with Digest (D14) |
| One surface, two targets | D22 S0: 4 lanes of 1000 and of 5000 objects presented within the main-thread budget ([03](03-runtime.md#performance)) on a real GPU; then the same code rendering offscreen in Electron at native size for export |
| Plugins | The D1 runtime: one external file decoder, one payload decoder and one resolver; the D4 schema against both built-in resolvers |
| Platform | Fractional HiDPI and the accessibility tree in Electron; Windows and macOS builds (D2) |

## Decisions taken for the foundation

Where the prototype and this playbook differed, or neither settled a question, the foundation took the simplest
option matching the prototype. Paths here are new-tree paths. Each can be revisited by the owner.

| Decision | Reason |
|----------|--------|
| Make targets keep the prototype's verbs (`web`, `desktop`, `build`, `tests*`, `check`, `help`) plus `check-fast` and `format` from [10](10-quality-gates.md#gates-must). No `sync` (dependencies install on first use); `run`/`run-dev` are `desktop`/`web`; `test-failed` is `TEST=<filter>` | One set of verbs; the prototype's need no setup step and no aliases |
| `scripts/cargo` runs cargo through the user's rustup, or installs a checkout-local rustup into `.cargo-cache/` when there is none; a cargo without rustup is ignored because it would not honor `rust-toolchain.toml`, which pins the toolchain | The prototype expected that folder to exist; a clean clone must build with no setup step |
| The logging gate is lints (Clippy `print_stdout`, `print_stderr`, `dbg_macro`; Oxlint `no-console`, off for `scripts/` and `desktop/`, whose console is their interface), not a script | The tools already enforce it |
| `scripts/` and `desktop/` JavaScript is formatted and linted with type-aware rules but not type-checked | Type-checking CommonJS needs Node and Electron type packages and a second TypeScript config; add them when that code grows |
| The architecture check reads `cargo metadata`; each crate declares `[package.metadata.ax-devil] layer`. Frontend import rules are added with the first frontend modules | Smallest check that enforces the layer direction and keeps layers 1–3 free of the HTTP server, which plays the toolkit's role on the backend |
| License gate: `cargo-deny` for crates and `scripts/check-js-licenses.cjs` for npm packages, one allow list in `deny.toml` | [13](13-security-packaging-ops.md#licensing) asks for a check of every dependency |
| Stale generated files fail CI through a clean-tree check after `make check`, not a local check | Locally they are regenerated by the build; the check covers every future generated file |
| The `rewrite` branch is the whole rewrite and the only PR that merges to `main`, at parity. Later work lands as commits on it or as PRs with base `rewrite`, never as separate merges to `main`. CI runs for PRs to and pushes on `main` and `rewrite` | `main` keeps the Python app until the rewrite replaces it in one reviewed merge |
| No pre-commit hooks; `make check-fast` locally and CI enforce the gates | Hooks need a setup step per clone |
| Every suite runs through `scripts/isolated`; it guards the `ax-devil` folders under the XDG config, data, cache and state homes (and the prototype's `~/.ax_devil`). Electron's data goes to `ax-devil-chromium` in the cache home, outside the guard | Every path the app uses resolves in `src/paths.rs` to one of those folders; whoever adds a storage folder updates both |
| Custom window frame (D7): a frameless Electron window whose in-page top bar is the title bar (title, menus, drag region, minimize/maximize/close through a minimal preload; absent in a plain browser) | The owner's window manager draws no title bar, so a native frame leaves no way to move or close the window |
| D10: accessibility target is WCAG 2.x AA contrast (4.5:1 text, 3:1 large text and component boundaries), every action reachable by keyboard, accessible names on icon-only controls | The design tokens are contrast-checked in both themes at M0 and the write-ui skill states the rule; the accessibility-tree pass is M8 |
| Zoom is the browser's; no app text-size setting. Electron zooms with Ctrl+=, Ctrl+- and Ctrl+0 and Chromium remembers it per host; layouts must fit the window minimum and zoom up to 200% | A web app inherits zoom, so the prototype's `ui.text_size` and its live refit are dropped; a config that still holds the key keeps it untouched |
| No WebAssembly target or shared Rust/WASM crates | Presentation is TypeScript (D22) and the CSP forbids WASM |
| No root `ARCHITECTURE.md`; the prototype's content lives in `docs/architecture/overview.md` | One home, per [11](11-docs-structure.md#docs-map) |
| Carried assets: catalogs in `assets/catalogs/`, icons and banner in `assets/`, the ONVIF sample in `fixtures/` | Packaged resources and test inputs, each in one place |
| Development ports 7330 (backend) and 7331 (frontend); the API version has one source in `crates/api-types`; the shell and supervisor read it from the generated `desktop/generated/api.json` | Distinct from the prototype's ports, so both can run; one source for the version |
| CI has an Ubuntu `check` job and a macOS frontend/backend job. Not yet: Windows, a lowest-dependency job (Cargo needs nightly for minimal versions), the release check (DT-05) | Added with packaging (D6, M9) or when the owner wants them earlier |

## Decisions taken for offline video

M1 took these with the owner. Paths here are new-tree paths. The rules they led to live with their modules
(`crates/media/README.md`, `crates/playback/README.md`, `docs/domain/invariants.md#offline-delivery`); the other
deviations from the reference are in `docs/parity.md`. Each decision can be revisited by the owner.

| Decision | Reason |
|----------|--------|
| FFmpeg decodes natively in the backend with `ffmpeg-next` (WTFPL, added to `deny.toml`) linking the system libraries, FFmpeg 6.1 to 9. Frames reach the frontend as raw I420 (NV12 or RGBA when the source needs it), converted to RGB by a WebGL2 shader. JPEG and WebCodecs were rejected. Hardware decode is not built | `ffmpeg-next` 9.0.0, WTFPL. `rsmpeg` 0.18 was the first choice but fails to compile against FFmpeg 9.0.2; `ffmpeg-next` builds and passes the media suite against both FFmpeg 9.0.2 and Ubuntu 24.04's 6.1.1. Spike (synthetic content, real GPU, headless): raw I420 with a YUV-to-RGB shader free-runs at 767 fps (1080p) and 135 fps (4K) and plays 4K30 with no dropped frames. JPEG q90 via `createImageBitmap` costs p50 18 ms (1080p) and 129 ms (4K) per frame and dropped 13 of 300 frames at 4K30. WebCodecs in software decodes H.264 at 146 fps (1080p) and 37 fps (4K); software HEVC is unsupported in Chromium on Linux. D6 still decides which FFmpeg build a bundle links (an LGPL build, dynamically). |
| Offline playback's clock and window: the rule is in `docs/domain/invariants.md#offline-delivery`; a viewer sends its window (`Want`: playhead, frames ahead and behind, direction), which replaces play, pause and speed messages | Presentation must follow the display refresh and be immune to socket jitter, and the frames are already in the viewer's buffer. Lanes in one viewer share one primary clock; live needs none and export is clock-free in the backend |
| Overlay data arrives as its own messages, joined with the frame in the frontend (items and maps: D26) | Changing a catalog or filter never resends pixels |
| D19: a viewer shows exactly one video, so the playback socket and the frame envelope are per viewer and carry no lane; only overlay messages name a source or lane. Two videos side by side are two viewers with no shared clock | The lanes of a viewer share every frame, so sending it once per lane would multiply the bytes for nothing; one session and one socket per viewer keeps the clock frontend-owned and the control messages lane-free |
| Cache root `$XDG_CACHE_HOME/ax-devil/` (`~/.cache/ax-devil/`), or `--cache-dir` | Resolved in `src/paths.rs` and guarded by `scripts/isolated` |
| D9 budgets: backend cache Auto is 25% of the memory available at startup, at least 256 MiB (1 GiB where it cannot be read, which is everywhere but Linux); frontend buffer 512 MiB per viewer, about 170 frames at 1080p and 43 at 4K; 2 decode workers per source; FFmpeg threads come from one budget of the machine's cores shared by every decoder of every open source (1 to 8 each). Sample data takes 10% of the viewer's frame budget and is evicted by playhead distance, before frames are. Target: resident memory at most the cache allowance plus 600 MB at 4K (not yet measured) | The owner accepted the proposed numbers. The backend cache is a share of free memory, so it adapts to the machine; the frontend buffer fixes the window size per video |
| D3: one process. Decode workers are threads of the backend | A thread stops at a frame boundary and shares the cache pool; no helper process is needed |
| D21: settings are one flat, sparse, versioned file in the XDG config folder; window state goes through the backend ([mechanics](../../crates/settings/README.md)) | An unreadable file stops startup instead of silently losing choices |

## Decisions taken for the overlay pipeline

The owner approved D22–D30 together; none is built (slices: [next priorities](#next-delivery-priorities)). Rules:
`docs/domain/invariants.md`; shape: `docs/architecture/overview.md#overlays`.

| Decision | Reason |
|----------|--------|
| D22: the backend supplies data only (decode, parse, store, index, frame map, selection, query) and does all work shared across sessions and lanes. All presentation (catalog and style evaluation, filters, label layout, glyph atlas, zoom, pan, theme, aspect, marks, picking) is TypeScript run per lane on the browser main thread. No WebAssembly, no native second renderer | Sessions and lanes view the same data differently, so per-view work must not cost backend resources; measured evaluation is fast enough (S0 confirms it before the switch); the CSP forbids WASM |
| D23: export and `catalog render` run the same presentation code in an offscreen Electron page, `catalog check` in Electron-as-Node. Rust freezes the inputs and encodes frames with source timestamps. Format and encoder (D6) are decided when export is built; default MP4/H.264 through the linked FFmpeg | One implementation for screen and export |
| D24: the core use case is one video with two different overlay sources, each lane with its own catalog, filter, theme, zoom and aspect; the same source in two lanes works too. Different videos stay out (D19) | Lane style is client state, so a shared source costs nothing on the wire |
| D25: catalog schema v4 replaces v3: flat rules plus ordered layers, label placement lists, one expression language for styles and frame filters, no `extends`. Layer order replaces the fixed shapes, text, labels order. Built-in catalogs are served over the socket like user catalogs. The format spec is yet to be written | The frontend compiles catalogs, so styles, frame filters and draw order need one language it evaluates alone |
| D26: Scene stays the store's data model. A Sample Item is the immutable encoding of one sample, keyed by source revision and sample; a Frame Map per lane assigns samples to frames. The backend owns tolerance, sticky and sequence selection; revisions replace per-seek epochs; colour-list attributes are a typed Scene value; class filters use a backend-computed class mask | Every lane of a source and every viewer shares items; lanes differ only in their maps; the decoder's class rules stay in Rust |
| D27: a lane never draws another sample's marks. During play it shows a loading state and the video is not held; after a seek or step a frame waits at most about 250 ms; export always waits. Supersedes "a frame is shown only when every visible lane holds its overlay" | A slow backend must neither freeze playback nor put marks on the wrong frame |
| D28: live uses a bounded deadline (jitter buffer); the gate covers all lanes sharing the viewer's video; the start value is 1.5 s until camera skew is measured | Late metadata must neither stall video nor land on the wrong frame |
| D29: ingest runs converter processes (any language, JSON Lines) with a manifest, an allowlisted environment, drained stderr, a 16 MiB line cap and process-group kill; a failure marks the lane incomplete and cache identity includes the script hash. The host groups equal-timestamp records and sorts at the end of ingest. Settles D1: no in-process Python bridge | A process boundary isolates and kills; the host alone writes the store |
| D30: file-scope queries stay a separate restricted backend query (a track that was car, then bus, matches both); the whole-file filter (IN-02) is built after the main slices | Sample Items hold the latest observation only, so a file-wide question cannot be answered from them |

## Milestones

Each exit is a commit on the rebuild branch with green CI and an updated `docs/parity.md`; the owner reviews the
diff range since the previous exit and signs it. Milestone listings schedule work; S rows enter an exit criterion only
when selected by the owner.

| M | Slice | Exit |
|---|-------|------|
| M0 | Day-one scaffold ([11](11-docs-structure.md#day-one-scaffold), done by the foundation); tokens; light/dark/auto; component kit seed; screenshot harness; fit-test guard | Empty window screenshots in both themes from CI; fit test passes |
| M1 | Skeleton: `local --video` through minimal item, Content, shared intake and resolution contracts into an in-memory Untitled workspace; clock-following playback, steps, seek; decoded-frame cache and its allowance (OF-06; the cache marks on the timeline and next-entry warm-up stay with M4); settings service; logging | Frame identity test; frame-cache and playback protocol tests; live theme switch passes the fit test |
| M2 | Overlay path: Scene model and serialization schema, decoder contract, one built-in decoder, overlay lookup and matching, catalogs with Standard and their presentation (D22), `catalog check/render`; the settings crate gets a typed accessor for the cache-size setting (D20), replacing the parsing in `src/video_cache.rs` | Canonical comparison passes for Standard's sheets ([09](09-testing.md#oracle-policy)) |
| M3 | Workspace persistence, recovery copy, store, row projection, Workspace view, split view, `open` | Lifecycle tests and recovery-copy crash safety, toolkit-free |
| M4 | Offline depth: lanes (several overlays on one video; playlist entries switch the video), playlists, both resolvers, hide, cache marks on the timeline, sticky, diagnostics, media tools, bookmarks, export | OF-02 parity case; alignment TSVs match the oracle; cache-build crash safety |
| M5 | Live: RTSP, ONVIF metadata, MQTT, DataHub, sync, status, scan, Live view | Fake-transport and burst tests; one manual camera session |
| M6 | Catalogs complete: six built-ins, manager, selection scopes, visibility, catalog window | Canonical comparison for all built-ins; catalog conformance suite ([09](09-testing.md#pyramid)) |
| M7 | Plugin runtime, all built-in decoders, external install/update/remove, Plugins view | The write-plugin example installs and decodes in a fresh process |
| M8 | Settings dialog, Quick Setup, Shortcuts dialog, accessibility pass, polish | Fit test for every window at the window minimum and at 200% zoom |
| M9 | Packaging and release; owner-selected S rows (benchmarks, release check, soak) | Every M row done or dropped by the owner in writing |

## Next delivery priorities

Status follows `docs/parity.md`, not milestone order: the current offline slice already includes lanes, both
resolvers, image sequences and catalog execution, while persistence and inspection remain unfinished. Next, close
remaining intake/playback gaps and establish durable workspace recovery; then complete inspection, bookmarks,
diagnostics and safe export. Bring ADF payload decoders forward for live work. External plugin execution and
packaging feasibility still need decisions before their consumers.

The overlay pipeline (D22–D30) goes first among overlay work: S0 the evaluator spike with its benches and S1 the data
wire and switch prove it at 4 lanes of 1000 and of 5000 objects; then S2 the remaining built-ins and `catalog check`,
S3 deleting the Rust render path, S4 lane presentation and user catalogs, S5 picking and the inspector, S6 export,
S7 converters, S8 file-scope queries, S9 live.

Every new slice extends SH-01–04: decide what follows the presenter, what stays private, and how removal or
permission changes revoke access. Replacement acceptance covers user workflows; existing S capabilities need an
explicit disposition before claiming all Python use cases are restored. Installation and upgrade of the actual
release artifact are release gates even though the reusable DT-05 tooling is S.
