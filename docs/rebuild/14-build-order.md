# 14 Build Order and Open Decisions

Delivery framing is in the [README](README.md#delivery). This file holds the stack criteria, D0, milestones and the
other decisions.

## Stack criteria

| Criterion | Ref |
|-----------|-----|
| Frame-accurate decode and seek, long-GOP H.264/H.265, 4K; Media Time from container timestamps, variable frame rate | [04](04-domain-rules.md#neutral-types-to-fix-early) |
| RTSP client exposing per-frame device capture time for video and metadata tracks (*current:* ONVIF replay header extension, requested with `onvifreplayext=1` in `data_sources/live/rtsp_source.py`; RTCP sender-report NTP mapping is the generic alternative); MQTT; WebSocket with Digest | LV-04, D14 |
| GPU video plus custom overlay drawing in one surface, and the same semantics offscreen at native size | [06](06-ui-ux.md#overlay-drawing) |
| Concurrency with a UI-thread hand-off and executor-bound destruction | [03](03-runtime.md#concurrency-guarantees-must) |
| Live theme and text-size changes, fractional HiDPI, accessibility API | [06](06-ui-ux.md) |
| Offscreen or private-display rendering, toolkit-level test input, window capture | [09](09-testing.md#isolation-must) |
| Strict typing, formatter, linter, dependency-rule checker | [10](10-quality-gates.md) |
| A plugin story compatible with D1 and D6 | [05](05-formats-and-extensions.md#plugin-runtime) |

## D0: stack choice

Run throwaway spikes against the criteria: decode and seek, RTSP/ONVIF capture
time, offscreen rendering of video plus overlays, plugin bridge feasibility if D1 keeps a bridge, and the D4 schema
against both built-in resolvers. The owner signs D0 on the results. Spike code is not committed to the new tree.

**Taken for the foundation PR: the stack of the owner's Rust prototype** (new tree): a Rust backend (axum on tokio)
holding all state and work; a React and TypeScript frontend built, linted, formatted and tested with Vite+ (Vitest,
Oxlint, Oxfmt) and styled with Tailwind; an Electron shell that owns windows and the backend process; one Cargo and
one pnpm workspace; Make as the task runner. Reasons: the prototype already showed one React UI serving browser and
desktop over HTTP and WebSockets from one Rust backend; Chromium from Electron instead of the system webview (Linux
WebKitGTK crashed during video support detection); H.264 decode through WebCodecs drawn with an overlay on a canvas;
native decode through FFmpeg (`ffmpeg-next`); an overlay rectangle rendered offscreen with `tiny-skia` and the same
geometry from one Rust function compiled natively and to WebAssembly; an RTSP session with `retina` delivering a
video frame; a Python decoder as a subprocess speaking versioned JSON lines; and Electron driven on Xvfb with window
screenshots. It meets the typing, formatter, linter and dependency-check criterion (Rust, strict TypeScript, rustfmt
and Oxfmt, Clippy and Oxlint, `scripts/check-architecture.cjs`).

Spikes still owed before the owner signs, each against its criterion above:

| Spike | What the prototype did not show |
|-------|---------------------------------|
| Decode and seek | Frame-accurate seek and stepping on long-GOP H.264/H.265, 4K, variable frame rate with Media Time from container timestamps; whether decode runs in WebCodecs or natively, which decides where frames and overlays meet |
| Live capture time | Per-frame device capture time from RTSP (ONVIF replay extension or RTCP sender reports) for video and the ONVIF metadata track with `retina`; MQTT; WebSocket with Digest (D14) |
| One surface, two targets | GPU video plus overlays in one surface (WebGL/WebGPU canvas) at full rate, and the same Drawing Instructions rendered offscreen at native size for export, within D9 |
| Plugins | The D1 runtime: one external file decoder, one payload decoder and one resolver; the D4 schema against both built-in resolvers |
| Platform | Fractional HiDPI and the accessibility tree in Electron; Windows and macOS builds (D2) |

## Decisions taken for the foundation

Where the prototype and this playbook differed, or neither settled a question, the foundation PR took the simplest
option matching the prototype. Paths here are new-tree paths. Each can be revisited by the owner.

| Decision | Reason |
|----------|--------|
| Make targets keep the prototype's verbs: `web`, `desktop`, `build`, `tests`, `tests-frontend`, `tests-backend`, `tests-integration`, `tests-desktop`, `check`, `help`, plus `check-fast` and `format` from [10](10-quality-gates.md#gates-must). No `sync` (dependencies install on first use), `run`/`run-dev` are `desktop`/`web`, `test-failed` is `TEST=<filter>` | One set of verbs; the prototype's need no setup step and no aliases |
| `scripts/cargo` runs cargo through the user's rustup, or installs a checkout-local rustup into `.cargo-cache/` when there is none; a cargo without rustup is ignored because it would not honor `rust-toolchain.toml`, which pins the toolchain | The prototype expected that folder to exist; a clean clone must build with no setup step |
| The logging gate is lints (Clippy `print_stdout`, `print_stderr`, `dbg_macro`; Oxlint `no-console`, off for `scripts/` and `desktop/`, whose console is their interface), not a script | The tools already enforce it |
| `scripts/` and `desktop/` JavaScript is formatted and linted with type-aware rules but not type-checked | Type-checking CommonJS needs Node and Electron type packages and a second TypeScript config; add them when that code grows |
| The architecture check reads `cargo metadata`; each crate declares `[package.metadata.ax-devil] layer`. Frontend import rules are added with the first frontend modules | Smallest check that enforces the layer direction and keeps layers 1–3 free of the HTTP server, which plays the toolkit's role on the backend |
| License gate: `cargo-deny` for crates and `scripts/check-js-licenses.cjs` for npm packages, one allow list in `deny.toml` | [13](13-security-packaging-ops.md#licensing) asks for a check of every dependency |
| Stale generated files fail CI through a clean-tree check after `make check`, not a local check | Locally they are regenerated by the build; the check covers every future generated file |
| No pre-commit hooks; `make check-fast` locally and CI enforce the gates | Hooks need a setup step per clone |
| Every suite runs through `scripts/isolated`; it guards `~/.ax_devil` and the `ax-devil` folders under the XDG config, data, cache and state homes. Electron's own data (Chromium profile, crash dumps) goes to `ax-devil-chromium` in the cache home instead, so a running session never trips the guard | The new app's storage folders are not chosen yet; whoever chooses them (M1) updates the guard |
| Native window frame; the prototype's custom title bar is not carried | D7's default |
| No WebAssembly target or shared Rust/WASM crates yet | The prototype's were spikes; the rendering spike decides whether a crate must also run in the frontend |
| No root `ARCHITECTURE.md`; the prototype's content lives in `docs/architecture/overview.md` | One home, per [11](11-docs-structure.md#docs-map) |
| Carried assets: catalogs in `assets/catalogs/`, icons and banner in `assets/`, the ONVIF sample in `fixtures/` | Packaged resources and test inputs, each in one place readable by Rust and the frontend |
| Development ports 7330 (backend) and 7331 (frontend); the API version has one source in `crates/api-types`; the shell and supervisor read it from the generated `desktop/generated/api.json` | Distinct from the prototype's ports, so both can run; one source for the version |
| CI has an Ubuntu `check` job and a macOS frontend/backend job. Not yet: Windows, a lowest-dependency job (Cargo needs nightly for minimal versions), the release check (DT-05) | Added with packaging (D6, M9) or when the owner wants them earlier |

## Milestones

Each exit is a commit on the rebuild branch with green CI and an updated `docs/parity.md`; the owner reviews the
diff range since the previous exit and signs it. Milestone listings schedule work; S rows enter an exit criterion only
when selected by the owner.

| M | Slice | Exit |
|---|-------|------|
| M0 | Day-one scaffold ([11](11-docs-structure.md#day-one-scaffold), done by the foundation PR); tokens; light/dark/auto and live text size; component kit seed; screenshot harness; fit-test guard | Empty window screenshots in both themes from CI; fit test passes |
| M1 | Skeleton: `local --video` through minimal item, Content, shared intake and resolution contracts into an in-memory Untitled workspace; clock-following playback, steps, seek; settings service; logging | Frame identity test; live theme and text-size switch passes the fit test |
| M2 | Overlay path: Scene model and serialization schema, decoder contract, one built-in decoder, overlay lookup and matching, catalog compiler with Standard, drawing preparation, `catalog check/render` | Canonical instruction comparison passes for Standard's sheets ([09](09-testing.md#oracle-policy)) |
| M3 | Workspace persistence, recovery copy, store, row projection, Workspace view, split view, `open` | Lifecycle tests and recovery-copy crash safety, toolkit-free |
| M4 | Offline depth: lanes (same and different videos), playlists, both resolvers, hide, frame cache, sticky, diagnostics, media tools, bookmarks, export | OF-02 parity case; alignment TSVs match the oracle; cache-build crash safety |
| M5 | Live: RTSP, ONVIF metadata, MQTT, DataHub, sync, status, scan, Live view | Fake-transport and burst tests; one manual camera session |
| M6 | Catalogs complete: six built-ins, manager, selection scopes, visibility, catalog window | Canonical comparison for all built-ins; catalog conformance suite ([09](09-testing.md#pyramid)) |
| M7 | Plugin runtime, all built-in decoders, external install/update/remove, Plugins view | The write-plugin example installs and decodes in a fresh process |
| M8 | Settings dialog, Quick Setup, Shortcuts dialog, accessibility pass, polish | Fit test for every window at both text-size extremes |
| M9 | Packaging and release; owner-selected S rows (benchmarks, release check, soak) | Every M row done or dropped by the owner in writing |

## Fixtures from the reference commit

Generated and committed as the oracle policy says ([09](09-testing.md#oracle-policy)); exports add timing references.

## Parity tracking

`docs/parity.md`: one line per row from [01](01-product-scope.md) with status (todo, in progress, done, dropped), test
reference and notes. Dropping an M row needs the owner's written decision. New features get a row in 01 first.

## Risks

| Risk | Mitigation |
|------|------------|
| Frame-accurate seeking and caching on long-GOP video | D0 spike; frame-identity and cache tests first |
| Overlay drawing cost | D0 spike; measurement against D9 in M2 |
| Matching and sticky semantics drift | Frame Identity rules as tests before code; oracle TSVs |
| Live transports have no equivalent in the new language (`ax-devil-rtsp`, `ax-devil-mqtt`, `ax-devil-device-api` in `pyproject.toml`) | D14 in the D0 spikes |

## Open decisions

The recommended default applies unless the owner objects. D5 and D16 are withdrawn; D13 became parity row AP-06.

| ID | Question | Recommended default | By |
|----|----------|---------------------|----|
| D0 | Language and UI stack | The prototype's stack, taken for the foundation ([above](#d0-stack-choice)); owner signs after the owed spikes | Before M1 |
| D1 | Plugin runtime; are unchanged third-party plugins supported (PL-05)? | Native built-ins; authors port third-party plugins to the new contract; a Python bridge is optional ([05](05-formats-and-extensions.md#plugin-runtime)) | D0 |
| D2 | Supported platforms? | Linux supported, including plugin management (Linux-only today); Windows and macOS build and pass the suite | D0 |
| D3 | One process or helper processes? | One process; next-entry warm-up may be a thread natively; a helper only for a plugin bridge | M1 |
| D4 | Resolver settings as a declared schema ([05](05-formats-and-extensions.md#resolver-contract))? | Yes | D0 |
| D6 | Packaging; bundled Python for plugins; GPL encoder in bundles? | AppImage or Flatpak plus source build; Python only if D1 keeps a bridge; non-GPL encoder in bundles | M2 |
| D7 | Custom frameless window? | Native frame default, custom frame optional | M8 |
| D8 | OS keychain for literal secrets in config and workspace items? | Yes, optional; `$VARIABLE` references stay default | M5 |
| D9 | Frame-time and memory budgets? | Proposals in [03](03-runtime.md#performance) on one named machine | M2 |
| D10 | Accessibility target? | WCAG 2.x AA contrast, full keyboard reach, accessible names | M0 |
| D11 | Localization? | English only; strings grouped per module | — |
| D12 | DataHub certificates (today: HTTPS/WSS without verification by default)? | Same for parity; pinning option later | M5 |
| D14 | Live transports in the new language? | Third-party RTSP/MQTT clients meeting the capture-time criterion plus a small Axis device-API module with temporary MQTT publisher setup and removal (LV-08) | D0 |
| D15 | Scene and Drawing Instruction dump script for the reference worktree? | Yes, throwaway; outputs committed with provenance | M2 |
| D17 | Remove `docs/rebuild/` at parity or keep as history? | Remove once every rule has its new home ([11](11-docs-structure.md#retiring-docsrebuild)) | M9 |
| D18 | When may workspace items expand `$VARIABLE` references ([13](13-security-packaging-ops.md#threat-model))? | Credentials bound to their host reference, else one confirmation showing the resolved host, remembered in user state. Behavior change from the reference | M3 |
