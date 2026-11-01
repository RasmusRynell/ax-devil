# Testing

What to test and how to judge a test is in the [write-tests skill](../../.agents/skills/write-tests/SKILL.md); the
test policy being ported is [09](../rebuild/09-testing.md).

## Running tests

```bash
make tests                         # Every suite
make tests-frontend TEST=App       # React component tests (Vitest, jsdom); TEST filters by name or file
make tests-backend TEST=info       # Rust tests, without and with the embedded frontend
make tests-integration             # Development supervisor (starts the services, stops the whole tree) and the desktop's argument forwarding
make tests-desktop                 # Electron app on a private display; screenshots in artifacts/screenshots/
make bench                         # Rust benchmarks (catalog render, lane frame); numbers on stdout, nothing asserted
make check                         # Every static check, then every suite
make check-fast                    # Static checks only, file-size check on changed files only
```

Targets install what they need and exit after one run. The first run downloads the Rust toolchain (into
`.cargo-cache/` and `.toolchain/` when no `rustup` is on `PATH`), crates, npm packages, Watchexec and cargo-deny
(both built into `.tools/`) and the Electron binary (into the user's download cache). After that, every target runs
offline as long as `Cargo.lock`, `pnpm-lock.yaml`, `rust-toolchain.toml` and the pinned tool versions in the
Makefile are unchanged. Test output and evidence go in `artifacts/`. The system FFmpeg libraries and clang that the
`media` crate builds against are prerequisites no target installs ([README](../../README.md#build-from-source)).

## Tests never touch the user's files or session

Every suite runs through `scripts/isolated`:

- `HOME` and the XDG config, data, cache, state and runtime folders point into a fresh temporary folder, removed
  afterwards. Toolchains keep their real locations.
- `DISPLAY`, `WAYLAND_DISPLAY` and the session bus are unset, so nothing can reach the user's desktop.
- `--display` starts a private Xvfb server for that run only (`xvfb-run -a`), so parallel runs never share a display
  or compete for focus. Without Xvfb the run fails; it never falls back to the user's display.
- Before and after, it records path, size and modification time under `~/.ax_devil` and the `ax-devil` folders in
  the user's XDG config, data, cache and state homes, and fails the run if anything changed.

Run any ad-hoc GUI check the same way, for example
`scripts/isolated --display pnpm --dir desktop exec electron . --ozone-platform=x11 --smoke-test`. What a virtual
display cannot verify (physical GPU, compositor, window manager, fractional scaling on a real screen) is reported as
not run.

## Desktop suite and screenshots

`make tests-desktop` builds the release app, starts Electron with `--smoke-test` and runs `desktop/smoke.cjs` in the
window: it waits for the app shell, then switches the theme to light and dark and saves
`artifacts/screenshots/main-<theme>.png`. Screenshots are captured only after the compositor has drawn a frame for
the current state; capturing earlier fails. It also captures the View menu and Settings dialog at the window
minimum, at normal zoom and 200%, then adds a fixture video through the Add Video command and asserts the canvas
shows a picture and that stepping changes it. It adds the fixture MOT Challenge playlist through the Add Playlist
dialog and asserts, on the real canvas, that every lane draws its boxes on frame 0 and none on the empty frame 5, in
both themes, at the window minimum and at 200% zoom. It also runs a sharing scenario (an owner, a watcher that joins
through a link, and a third window that lists the shared Session) and asserts that no page of the release app
reports a Content Security Policy violation. In smoke
mode the desktop's listener for other machines takes a free loopback port through the hidden `--share-address`, so
nothing leaves the machine. New windows join the harness as they land ([DT-02](../parity.md#developer-tooling)).

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

## CI

`.github/workflows/ci.yml` runs on pull requests to and pushes on `main` and `rewrite`, and on `v*` tags. `check` (Ubuntu 24.04) runs `make check`,
fails when generated files are stale, checks on tags that the tag matches the `Cargo.toml` version, and uploads the
screenshots. `test-macos` runs the frontend and backend suites. Both jobs install the FFmpeg development libraries
and clang that the `media` crate builds against (`pkg-config` finds FFmpeg). A newer push to a pull request cancels
its superseded run. Windows, the lowest-dependency job and the release check are not set up yet
([14](../rebuild/14-build-order.md#decisions-taken-for-the-foundation)).
