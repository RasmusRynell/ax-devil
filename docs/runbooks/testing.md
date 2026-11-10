# Testing

What to test and how to judge a test is in the [write-tests skill](../../.agents/skills/write-tests/SKILL.md); the
test pyramid and oracle policy are in [09](../rebuild/09-testing.md).

## Running tests

```bash
make test                          # Every suite
make test-frontend TEST=App        # React component tests (Vitest, jsdom); TEST filters by name or file
make test-backend TEST=info        # Rust tests, without and with the embedded frontend
make test-integration              # Development supervisor (starts the services, stops the whole tree) and the desktop's argument forwarding
make test-desktop                  # Electron app on a private display; screenshots in artifacts/screenshots/
make bench                         # Rust benchmarks (sample item encode); numbers on stdout, nothing asserted
make bench-export                  # Export speed through the real render page, SwiftShader and GPU; numbers on stdout
make check                         # The gate: every static check, then every suite
make check-fast                    # Static checks only, file-size check on changed files only
```

Targets install what they need and exit after one run. The first run downloads the Rust toolchain (into
`.cargo-cache/` and `.toolchain/` when no `rustup` is on `PATH`), crates, npm packages, Watchexec and cargo-deny (built
into `.tools/` unless `PATH` has Watchexec 2.x, or cargo-deny at the Makefile's pinned version) and the Electron binary
(into the user's download cache). After that, every target runs offline as long as `Cargo.lock`, `pnpm-lock.yaml`,
`rust-toolchain.toml` and the pinned tool versions in the Makefile are unchanged. Test output and evidence go in
`artifacts/`. The system FFmpeg libraries and clang that the
`media` crate builds against are prerequisites no target installs ([README](../../README.md#build-from-source)).

Debug and test builds carry line tables only (backtraces keep `file:line`; dependencies have none) to keep `target/`
small. For a debugger, add `--profile dev-full` to a `scripts/cargo` build or test; it builds into `target/dev-full`.

A crate's integration tests are one binary, `tests/it/main.rs`: add a file there and a `mod` line, not a new `tests/*.rs`. The root crate keeps the tests that set environment variables in two more, `tests/export/` and `tests/live_transport.rs`.

## Tests never touch the user's files or session

Every suite runs through `scripts/isolated`:

- `HOME` and the XDG config, data, cache, state and runtime folders point into a fresh temporary folder, removed
  afterwards. Toolchains keep their real locations. Tests therefore load no user plugins, and the private settings
  turn the startup network scan off; devices and transports are faked.
- `DISPLAY`, `WAYLAND_DISPLAY` and the session bus are unset, so nothing can reach the user's desktop.
- `--display` starts a private Xvfb server for that run only (`xvfb-run -a`), so parallel runs never share a display
  or compete for focus. Without Xvfb the run fails; it never falls back to the user's display.
- Before and after, it records path, size and modification time under `~/.ax_devil` and the `ax-devil` folders in
  the user's XDG config, data, cache and state homes, and fails the run if anything changed.

Run any ad-hoc GUI check the same way: `make app ISOLATE=1 ARGS=...` runs the release app on a private display and
`make cli ISOLATE=1 ARGS=...` the backend. What a virtual display cannot verify (physical GPU, compositor, window manager, fractional scaling on a real screen) is reported as
not run.

What an ad-hoc check creates outside `artifacts/` and the run's temporary folder is removed when the check finishes
(anything over ~100 MB first), and no process it started (test runner, Electron, browser, dev server) is left running.

## Export end to end

`tests/export/export.rs` and `tests/export/catalog_check.rs` run the real Electron render page, forced to SwiftShader so pixels do
not depend on the machine's graphics (`AX_DEVIL_RENDER_GRAPHICS=software`), through the routes and the command, and
read the written MP4 and PNG back with `media`. They skip, saying why on stderr, when Electron or the built render
page is missing; `make test-backend` builds both and sets `AX_DEVIL_REQUIRE_ELECTRON`, which makes a skip a failure.

## Plugin tests

`crates/plugins/tests` and `tests/it/plugins.rs` run the plugins in `fixtures/plugins` as real processes. The
Python ones need `python3`; without it those tests skip, saying why on stderr, unless `AX_DEVIL_REQUIRE_PYTHON` is
set, which makes the skip a failure.

## Desktop suite and screenshots

`make test-desktop` builds the debug backend, starts Electron with `--smoke-test` and runs `desktop/smoke.cjs` in the
window: it waits for the app shell, then switches the theme to light and dark and saves
`artifacts/screenshots/main-<theme>.png`. Screenshots are captured only after the compositor has drawn a frame for
the current state; capturing earlier fails. It also captures the View menu and Settings dialog at the window
minimum, at normal zoom and 200%, then adds a fixture video through the Add Video command and asserts the canvas
shows a picture and that stepping changes it. On that video it opens the export dialog with Ctrl+E and checks that Export asks the native save dialog and
that the dialog fits the window minimum at 200% zoom in both themes. It adds the fixture MOT Challenge playlist through the Add Playlist
dialog and asserts, on the real canvas, that every lane draws its boxes on frame 0 and none on the empty frame 5, in
both themes, at the window minimum and at 200% zoom. It also runs a sharing scenario (an owner, a watcher that joins
through a link, and a third window that lists the shared Session) and asserts that no page of the app
reports a Content Security Policy violation. In smoke
mode the desktop's listener for other machines takes a free loopback port through the hidden `--share-address`, so
nothing leaves the machine. `desktop/fit-coverage.test.cjs` fails until each new dialog names the scenario that fit-checks it, or a reason ([DT-02](../parity/app.md#developer-tooling)).

## Sharing across two machines

Automated checks cannot cut a real network or open a real LAN listener, so run this by hand on release builds
(`make build`; a development build serves no frontend) when sharing changes. Host A and host B must reach each other.

1. On host A, start sharing in the Sessions view of the app. Alternatively run `ax-devil serve --bind 0.0.0.0` on
   A, open its printed link in a browser on B and start sharing there.
2. On B, open the join link in a browser, then open it again in the app: `ax-devil app --server <link>`, or pass the
   `ax-devil://open?url=<encoded link>` link as an argument. The app asks before it opens.
3. Check that the plain HTTP warning was logged on A, that both cursors show on each side, and that follow, explore
   and Follow presenter behave as in the smoke scenario.
4. Cut a watcher's network for 10 s: it resumes with the same Guest N. Cut it for 60 s (the server notices after 25 s
   and holds the seat 30 s more): the owner's list drops the seat.
5. With the owner in a browser on B and the server on A, cut B's network for 10 s: the session continues, with the
   watchers seeing the owner away meanwhile. Cut it for 60 s: the session ends and watchers are told the owner
   left (`OwnerGone`).
6. With a desktop owner, close its window without stopping sharing: watchers must not stay on a live session. They see
   the owner away and then the session end, or lose the connection once the app's backend has stopped.

## Fixtures

Committed inputs live in `fixtures/` with their provenance and license. Each test owns its temporary files and
caches; nothing is shared between tests through the filesystem.

`fixtures/session/` holds Session Docs, steps and seat rights written by the backend's own projection (`src/session/frontend_fixtures`). Backend tests rewrite them, and `make check` fails when running it changes them, so a clean checkout (CI) catches a stale committed one. Frontend tests take backend-derived values from them through `frontend/src/session/fakeScenario.ts`; a new shape is a new scenario in `src/session/frontend_fixtures`, never built or patched in TypeScript. A command a test sends changes the fake's Doc only through a recorded step; one with none fails the test unless it is in `UNRECORDED` in `fakeSession.ts` (random ids or file work, so no live edit).

## CI

`make check` is the gate. CI runs exactly it once, on Linux; the per-OS jobs only build and smoke-test the packaged
app. `.github/workflows/ci.yml` runs on pull requests to and pushes on `main` and `rewrite`, and on `v*` tags, with
every job starting at once:

- `check` (Ubuntu 24.04) runs `make check` once: no other job runs a check or a test. It builds
  and tests against the same pinned FFmpeg the Linux package ships (`FFMPEG_DIR`, shared cache entry), installs clang
  for the `media` crate's bindings, and uploads the screenshots. The make target fails when its checks leave generated files changed. Only pushes save the Cargo cache;
  pull requests restore it (a copy per pull request ref would pass the repository's 10 GB cache cap).
- `package` (Linux, Windows, macOS) runs `make package` and `make smoke-packaged` against a pinned shared FFmpeg
  (downloaded for Linux and Windows, built from source on macOS, both cached). Windows runs make from Git Bash.
- `version` and `release` run on tags only. `version` runs `make verify-release-version`; `release` waits for all
  three other jobs and drafts the release from the packages.

A newer push to a pull request cancels its superseded run. Versions are pinned once each: Node in `.node-version`,
pnpm in `packageManager`, Rust in `rust-toolchain.toml`, actions by commit SHA in the workflow.
