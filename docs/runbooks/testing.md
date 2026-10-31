# Testing

What to test and how to judge a test is in the [write-tests skill](../../.agents/skills/write-tests/SKILL.md); the
test policy being ported is [09](../rebuild/09-testing.md).

## Running tests

```bash
make tests                         # Every suite
make tests-frontend TEST=App       # React component tests (Vitest, jsdom); TEST filters by name or file
make tests-backend TEST=info       # Rust tests, without and with the embedded frontend
make tests-integration             # Development supervisor: starts the services and stops the whole tree
make tests-desktop                 # Electron app on a private display; screenshots in artifacts/screenshots/
make check                         # Every static check, then every suite
make check-fast                    # Static checks only, file-size check on changed files only
```

Targets install what they need and exit after one run. The first run downloads the Rust toolchain (into
`.cargo-cache/` and `.toolchain/` when no `rustup` is on `PATH`), crates, npm packages, Watchexec and cargo-deny
(both built into `.tools/`) and the Electron binary (into the user's download cache). After that, every target runs
offline as long as `Cargo.lock`, `pnpm-lock.yaml`, `rust-toolchain.toml` and the pinned tool versions in the
Makefile are unchanged. Test output and evidence go in `artifacts/`.

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
minimum, at normal zoom and 200%. New windows join the harness as they land
([DT-02](../parity.md#developer-tooling)).

## Fixtures

Committed inputs live in `fixtures/` with their provenance and license. Each test owns its temporary files and
caches; nothing is shared between tests through the filesystem.

## CI

`.github/workflows/ci.yml` runs on pull requests to and pushes on `main` and `rewrite`, and on `v*` tags. `check` (Ubuntu 24.04) runs `make check`,
fails when generated files are stale, checks on tags that the tag matches the `Cargo.toml` version, and uploads the
screenshots. `test-macos` runs the frontend and backend suites. A newer push to a pull request cancels its superseded
run. Windows, the lowest-dependency job and the release check are not set up yet
([14](../rebuild/14-build-order.md#decisions-taken-for-the-foundation)).
